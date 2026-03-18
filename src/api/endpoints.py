from fastapi import APIRouter, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime
import json
import uuid
from typing import Optional

from src.core.config import config
from src.core.logging import logger
from src.core.client import OpenAIClient
from src.models.claude import ClaudeMessagesRequest, ClaudeTokenCountRequest
from src.conversion.request_converter import convert_claude_to_openai
from src.conversion.response_converter import (
    convert_openai_to_claude_response,
    convert_openai_streaming_to_claude_with_cancellation,
)
from src.core.model_manager import model_manager
from src.core.web_search import execute_web_search

router = APIRouter()

# Get custom headers from config
custom_headers = config.get_custom_headers()

openai_client = OpenAIClient(
    config.openai_api_key,
    config.openai_base_url,
    config.request_timeout,
    config.read_timeout,
    api_version=config.azure_api_version,
    custom_headers=custom_headers,
)

async def validate_api_key(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
    """Validate the client's API key from either x-api-key header or Authorization header."""
    client_api_key = None
    
    # Extract API key from headers
    if x_api_key:
        client_api_key = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        client_api_key = authorization.replace("Bearer ", "")
    
    # Skip validation if PROXY_API_KEY is not set in the environment
    if not config.client_api_key:
        return
        
    # Validate the client API key
    if not client_api_key or not config.validate_client_api_key(client_api_key):
        logger.warning(f"Invalid API key provided by client")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key. Please provide a valid Proxy API key."
        )

@router.post("/v1/messages")
async def create_message(request: ClaudeMessagesRequest, http_request: Request, _: None = Depends(validate_api_key)):
    try:
        logger.debug(
            f"Processing Claude request: model={request.model}, stream={request.stream}"
        )

        # Generate unique request ID for cancellation tracking
        request_id = str(uuid.uuid4())

        # Convert Claude request to OpenAI format
        openai_request = convert_claude_to_openai(request, model_manager)

        # Check if client disconnected before processing
        if await http_request.is_disconnected():
            raise HTTPException(status_code=499, detail="Client disconnected")

        if request.stream:
            # Streaming response with web_search interception
            try:
                openai_stream = openai_client.create_chat_completion_stream(
                    openai_request, request_id
                )
                return StreamingResponse(
                    _streaming_with_web_search(
                        openai_stream,
                        openai_request,
                        request,
                        http_request,
                        request_id,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*",
                    },
                )
            except HTTPException as e:
                # Convert to proper error response for streaming
                logger.error(f"Streaming error: {e.detail}")
                import traceback

                logger.error(traceback.format_exc())
                error_message = openai_client.classify_openai_error(e.detail)
                error_response = {
                    "type": "error",
                    "error": {"type": "api_error", "message": error_message},
                }
                return JSONResponse(status_code=e.status_code, content=error_response)
        else:
            # Non-streaming response with web_search loop
            openai_response = await _non_streaming_with_web_search(
                openai_request, request_id, http_request
            )
            claude_response = convert_openai_to_claude_response(
                openai_response, request
            )
            return claude_response
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        logger.error(f"Unexpected error processing request: {e}")
        logger.error(traceback.format_exc())
        error_message = openai_client.classify_openai_error(str(e))
        raise HTTPException(status_code=500, detail=error_message)


def _extract_web_search_calls(openai_response: dict) -> list:
    """Extract web_search tool calls from an OpenAI response."""
    choices = openai_response.get("choices", [])
    if not choices:
        return []
    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls", []) or []
    return [
        tc for tc in tool_calls
        if tc.get("function", {}).get("name") == "web_search"
    ]


async def _non_streaming_with_web_search(
    openai_request: dict, request_id: str, http_request: Request, max_rounds: int = 3
) -> dict:
    """
    Non-streaming request with web_search tool call loop.
    When the model calls web_search, execute the search and feed results back.
    """
    current_request = openai_request.copy()

    for round_num in range(max_rounds):
        if await http_request.is_disconnected():
            raise HTTPException(status_code=499, detail="Client disconnected")

        openai_response = await openai_client.create_chat_completion(
            current_request, f"{request_id}_r{round_num}"
        )

        web_search_calls = _extract_web_search_calls(openai_response)

        if not web_search_calls:
            # No web_search calls — return the response as-is
            return openai_response

        # Execute web searches and build tool result messages
        logger.info(f"Web search round {round_num + 1}: {len(web_search_calls)} search(es)")

        # Append the assistant's message (with tool_calls) to conversation
        assistant_msg = openai_response["choices"][0]["message"]
        current_request["messages"].append(assistant_msg)

        # Execute each web_search and append tool results
        for tc in web_search_calls:
            func_data = tc.get("function", {})
            try:
                args = json.loads(func_data.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            query = args.get("query", "")
            logger.info(f"Executing web search: '{query}'")
            search_result = await execute_web_search(query)

            current_request["messages"].append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(search_result, ensure_ascii=False),
            })

    # If we exhausted max_rounds, return the last response
    return openai_response


async def _streaming_with_web_search(
    initial_stream,
    openai_request: dict,
    original_request: ClaudeMessagesRequest,
    http_request: Request,
    request_id: str,
    max_rounds: int = 3,
):
    """
    Streaming response with web_search interception.

    Strategy:
    1. Collect the first streaming response fully
    2. If it contains web_search tool calls, execute them silently
    3. Feed results back and stream the final response to client
    """
    from src.core.constants import Constants

    for round_num in range(max_rounds):
        if round_num == 0:
            stream = initial_stream
        else:
            stream = openai_client.create_chat_completion_stream(
                openai_request, f"{request_id}_r{round_num}"
            )

        # Collect the full response to check for web_search calls
        collected_text = ""
        collected_tool_calls = {}  # index -> {id, name, arguments}
        final_finish_reason = None

        async for line in stream:
            if not line.strip():
                continue
            if line.startswith("data: "):
                chunk_data = line[6:]
                if chunk_data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_data)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                except json.JSONDecodeError:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                if delta.get("content"):
                    collected_text += delta["content"]

                if delta.get("tool_calls"):
                    for tc_delta in delta["tool_calls"]:
                        tc_index = tc_delta.get("index", 0)
                        if tc_index not in collected_tool_calls:
                            collected_tool_calls[tc_index] = {
                                "id": None, "name": None, "arguments": ""
                            }
                        tc = collected_tool_calls[tc_index]
                        if tc_delta.get("id"):
                            tc["id"] = tc_delta["id"]
                        func_data = tc_delta.get("function", {})
                        if func_data.get("name"):
                            tc["name"] = func_data["name"]
                        if func_data.get("arguments"):
                            tc["arguments"] += func_data["arguments"]

                if finish_reason:
                    final_finish_reason = finish_reason

        # Check if any tool calls are web_search
        web_search_calls = [
            tc for tc in collected_tool_calls.values()
            if tc.get("name") == "web_search" and tc.get("id")
        ]

        if not web_search_calls:
            # No web_search — we need to replay this response as Claude SSE to the client
            # Build a synthetic OpenAI-style response and stream it
            all_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in collected_tool_calls.values()
                if tc.get("id") and tc.get("name")
            ]

            async for event in _replay_as_claude_stream(
                collected_text, all_tool_calls, final_finish_reason, original_request
            ):
                yield event
            return

        # Has web_search calls — execute searches silently
        logger.info(f"Streaming web search round {round_num + 1}: {len(web_search_calls)} search(es)")

        # Build the assistant message with ALL tool calls
        all_tool_calls_for_msg = []
        for tc in collected_tool_calls.values():
            if tc.get("id") and tc.get("name"):
                all_tool_calls_for_msg.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })

        assistant_msg = {"role": "assistant", "content": collected_text or None}
        if all_tool_calls_for_msg:
            assistant_msg["tool_calls"] = all_tool_calls_for_msg

        openai_request["messages"].append(assistant_msg)

        # Execute web searches and add tool results
        for tc in collected_tool_calls.values():
            if not tc.get("id"):
                continue

            if tc.get("name") == "web_search":
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                query = args.get("query", "")
                logger.info(f"Executing web search: '{query}'")
                search_result = await execute_web_search(query)
                openai_request["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(search_result, ensure_ascii=False),
                })
            else:
                # Non-web_search tool call — this shouldn't happen in practice
                # since we only intercept web_search, but handle gracefully
                openai_request["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps({"error": "Tool not handled by proxy"}),
                })

    # Exhausted max rounds — stream whatever we have
    async for event in _replay_as_claude_stream(
        collected_text, [], final_finish_reason, original_request
    ):
        yield event


async def _replay_as_claude_stream(
    text: str,
    tool_calls: list,
    finish_reason: str,
    original_request: ClaudeMessagesRequest,
):
    """
    Replay collected response content as Claude SSE events.
    This is used when we collected a streaming response to check for web_search,
    and now need to send the final result to the client in Claude format.
    """
    from src.core.constants import Constants

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    # message_start
    yield f"event: {Constants.EVENT_MESSAGE_START}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_START, 'message': {'id': message_id, 'type': 'message', 'role': Constants.ROLE_ASSISTANT, 'model': original_request.model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}}, ensure_ascii=False)}\n\n"

    # content_block_start for text
    yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': 0, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_PING}\ndata: {json.dumps({'type': Constants.EVENT_PING}, ensure_ascii=False)}\n\n"

    # Stream text content in chunks to simulate streaming
    if text:
        chunk_size = 20  # characters per chunk
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': 0, 'delta': {'type': Constants.DELTA_TEXT, 'text': chunk}}, ensure_ascii=False)}\n\n"

    # content_block_stop for text
    yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': 0}, ensure_ascii=False)}\n\n"

    # Tool calls (non-web_search ones that should be forwarded to Claude Code client)
    tool_block_index = 0
    for tc in tool_calls:
        tool_block_index += 1
        func_data = tc.get("function", {})

        # content_block_start
        yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': tool_block_index, 'content_block': {'type': Constants.CONTENT_TOOL_USE, 'id': tc.get('id', f'tool_{uuid.uuid4()}'), 'name': func_data.get('name', ''), 'input': {}}}, ensure_ascii=False)}\n\n"

        # Send arguments as input_json_delta
        args_str = func_data.get("arguments", "{}")
        if args_str:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': tool_block_index, 'delta': {'type': Constants.DELTA_INPUT_JSON, 'partial_json': args_str}}, ensure_ascii=False)}\n\n"

        # content_block_stop
        yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': tool_block_index}, ensure_ascii=False)}\n\n"

    # Determine stop reason
    stop_reason = Constants.STOP_END_TURN
    if tool_calls:
        stop_reason = Constants.STOP_TOOL_USE
    elif finish_reason == "length":
        stop_reason = Constants.STOP_MAX_TOKENS

    # message_delta
    yield f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_DELTA, 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'input_tokens': 0, 'output_tokens': 0}}, ensure_ascii=False)}\n\n"

    # message_stop
    yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_STOP}, ensure_ascii=False)}\n\n"


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: ClaudeTokenCountRequest, _: None = Depends(validate_api_key)):
    try:
        # For token counting, we'll use a simple estimation
        # In a real implementation, you might want to use tiktoken or similar

        total_chars = 0

        # Count system message characters
        if request.system:
            if isinstance(request.system, str):
                total_chars += len(request.system)
            elif isinstance(request.system, list):
                for block in request.system:
                    if hasattr(block, "text"):
                        total_chars += len(block.text)

        # Count message characters
        for msg in request.messages:
            if msg.content is None:
                continue
            elif isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, "text") and block.text is not None:
                        total_chars += len(block.text)

        # Rough estimation: 4 characters per token
        estimated_tokens = max(1, total_chars // 4)

        return {"input_tokens": estimated_tokens}

    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "openai_api_configured": bool(config.openai_api_key),
        "api_key_valid": config.validate_api_key(),
        "client_api_key_validation": bool(config.client_api_key),
    }


@router.get("/test-connection")
async def test_connection():
    """Test API connectivity to OpenAI"""
    try:
        # Simple test request to verify API connectivity
        test_response = await openai_client.create_chat_completion(
            {
                "model": config.small_model,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 5,
            }
        )

        return {
            "status": "success",
            "message": "Successfully connected to OpenAI API",
            "model_used": config.small_model,
            "timestamp": datetime.now().isoformat(),
            "response_id": test_response.get("id", "unknown"),
        }

    except Exception as e:
        logger.error(f"API connectivity test failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "error_type": "API Error",
                "message": str(e),
                "timestamp": datetime.now().isoformat(),
                "suggestions": [
                    "Check your OPENAI_API_KEY is valid",
                    "Verify your API key has the necessary permissions",
                    "Check if you have reached rate limits",
                ],
            },
        )


@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Claude-to-OpenAI API Proxy v1.0.0",
        "status": "running",
        "config": {
            "openai_base_url": config.openai_base_url,
            "max_tokens_limit": config.max_tokens_limit,
            "api_key_configured": bool(config.openai_api_key),
            "client_api_key_validation": bool(config.client_api_key),
            "big_model": config.big_model,
            "small_model": config.small_model,
        },
        "endpoints": {
            "messages": "/v1/messages",
            "count_tokens": "/v1/messages/count_tokens",
            "health": "/health",
            "test_connection": "/test-connection",
        },
    }
