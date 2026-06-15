import json
import uuid
import traceback
import logging
from fastapi import HTTPException, Request
from src.core.constants import Constants
from src.models.claude import ClaudeMessagesRequest

logger = logging.getLogger(__name__)


def convert_openai_to_claude_response(
    openai_response: dict, original_request: ClaudeMessagesRequest
) -> dict:
    """Convert OpenAI response to Claude format."""

    # Extract response data
    choices = openai_response.get("choices", [])
    if not choices:
        raise HTTPException(status_code=500, detail="No choices in OpenAI response")

    choice = choices[0]
    message = choice.get("message", {})

    # Build Claude content blocks
    content_blocks = []

    # Add reasoning content if present (专门针对 moonshotai/kimi-k2.6 模型)
    # 转换为 Claude 的 thinking 块，以便在多轮对话中保留
    reasoning_content = message.get("reasoning_content")
    if reasoning_content is not None:
        thinking_block = {
            "type": Constants.CONTENT_THINKING,
            "thinking": reasoning_content
        }
        content_blocks.append(thinking_block)

    # Add text content
    text_content = message.get("content")
    if text_content is not None:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": text_content})

    # Add tool calls
    tool_calls = message.get("tool_calls", []) or []
    for tool_call in tool_calls:
        if tool_call.get("type") == Constants.TOOL_FUNCTION:
            function_data = tool_call.get(Constants.TOOL_FUNCTION, {})
            try:
                arguments = json.loads(function_data.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {"raw_arguments": function_data.get("arguments", "")}

            content_blocks.append(
                {
                    "type": Constants.CONTENT_TOOL_USE,
                    "id": tool_call.get("id", f"tool_{uuid.uuid4()}"),
                    "name": function_data.get("name", ""),
                    "input": arguments,
                }
            )

    # Ensure at least one content block
    if not content_blocks:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": ""})

    # Map finish reason
    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = {
        "stop": Constants.STOP_END_TURN,
        "length": Constants.STOP_MAX_TOKENS,
        "tool_calls": Constants.STOP_TOOL_USE,
        "function_call": Constants.STOP_TOOL_USE,
    }.get(finish_reason, Constants.STOP_END_TURN)

    # Build Claude response
    claude_response = {
        "id": openai_response.get("id", f"msg_{uuid.uuid4()}"),
        "type": "message",
        "role": Constants.ROLE_ASSISTANT,
        "model": original_request.model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": openai_response.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": openai_response.get("usage", {}).get(
                "completion_tokens", 0
            ),
        },
    }

    return claude_response


async def convert_openai_streaming_to_claude(
    openai_stream, original_request: ClaudeMessagesRequest, logger
):
    """Convert OpenAI streaming response to Claude streaming format."""

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    # Send initial SSE events
    yield f"event: {Constants.EVENT_MESSAGE_START}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_START, 'message': {'id': message_id, 'type': 'message', 'role': Constants.ROLE_ASSISTANT, 'model': original_request.model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': 0, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_PING}\ndata: {json.dumps({'type': Constants.EVENT_PING}, ensure_ascii=False)}\n\n"

    # Process streaming chunks
    text_block_index = 0
    reasoning_block_index = None  # 推理内容块的索引
    tool_block_counter = 0
    current_tool_calls = {}
    final_stop_reason = Constants.STOP_END_TURN
    reasoning_started = False  # 标记推理内容是否已开始
    text_block_started = False  # 标记文本块是否已开始（在推理块之后）

    try:
        async for line in openai_stream:
            if line.strip():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(chunk_data)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Failed to parse chunk: {chunk_data}, error: {e}"
                        )
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason")

                    # Handle reasoning content delta (专门针对 moonshotai/kimi-k2.6 模型)
                    # 将推理内容转换为 Claude 的 thinking 块
                    if delta and "reasoning_content" in delta and delta["reasoning_content"] is not None:
                        # 如果是第一个推理内容块，发送 content_block_start 事件
                        if not reasoning_started:
                            reasoning_block_index = text_block_index
                            # 发送 thinking 块的开始事件
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': reasoning_block_index, 'content_block': {'type': Constants.CONTENT_THINKING, 'thinking': ''}}, ensure_ascii=False)}\n\n"
                            reasoning_started = True

                        # 发送推理内容增量
                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': reasoning_block_index, 'delta': {'type': Constants.DELTA_THINKING, 'thinking': delta['reasoning_content']}}, ensure_ascii=False)}\n\n"

                    # Handle text delta
                    if delta and "content" in delta and delta["content"] is not None:
                        # 确定当前文本块的索引
                        current_text_index = text_block_index
                        if reasoning_started:
                            # 如果之前有推理内容，文本块索引需要递增
                            current_text_index = reasoning_block_index + 1
                            # 如果这是第一次收到普通文本，需要发送新的文本块开始事件
                            if not text_block_started:
                                # 先关闭推理块
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': reasoning_block_index}, ensure_ascii=False)}\n\n"
                                # 开始文本块
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': current_text_index, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"
                                text_block_started = True
                                text_block_index = current_text_index

                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': current_text_index, 'delta': {'type': Constants.DELTA_TEXT, 'text': delta['content']}}, ensure_ascii=False)}\n\n"

                    # Handle tool call deltas with improved incremental processing
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            tc_index = tc_delta.get("index", 0)
                            
                            # Initialize tool call tracking by index if not exists
                            if tc_index not in current_tool_calls:
                                current_tool_calls[tc_index] = {
                                    "id": None,
                                    "name": None,
                                    "args_buffer": "",
                                    "json_sent": False,
                                    "claude_index": None,
                                    "started": False
                                }
                            
                            tool_call = current_tool_calls[tc_index]
                            
                            # Update tool call ID if provided
                            if tc_delta.get("id"):
                                tool_call["id"] = tc_delta["id"]
                            
                            # Update function name and start content block if we have both id and name
                            function_data = tc_delta.get(Constants.TOOL_FUNCTION, {})
                            if function_data.get("name"):
                                tool_call["name"] = function_data["name"]
                            
                            # Start content block when we have complete initial data
                            if (tool_call["id"] and tool_call["name"] and not tool_call["started"]):
                                tool_block_counter += 1
                                claude_index = text_block_index + tool_block_counter
                                tool_call["claude_index"] = claude_index
                                tool_call["started"] = True
                                
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': claude_index, 'content_block': {'type': Constants.CONTENT_TOOL_USE, 'id': tool_call['id'], 'name': tool_call['name'], 'input': {}}}, ensure_ascii=False)}\n\n"
                            
                            # Handle function arguments — forward each fragment immediately
                            if "arguments" in function_data and tool_call["started"] and function_data["arguments"] is not None:
                                args_chunk = function_data["arguments"]
                                tool_call["args_buffer"] += args_chunk
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': tool_call['claude_index'], 'delta': {'type': Constants.DELTA_INPUT_JSON, 'partial_json': args_chunk}}, ensure_ascii=False)}\n\n"

                    # Handle finish reason
                    if finish_reason:
                        if finish_reason == "length":
                            final_stop_reason = Constants.STOP_MAX_TOKENS
                        elif finish_reason in ["tool_calls", "function_call"]:
                            final_stop_reason = Constants.STOP_TOOL_USE
                        elif finish_reason == "stop":
                            final_stop_reason = Constants.STOP_END_TURN
                        else:
                            final_stop_reason = Constants.STOP_END_TURN
                        break

    except Exception as e:
        # Handle any streaming errors gracefully
        logger.error(f"Streaming error: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        error_event = {
            "type": "error",
            "error": {"type": "api_error", "message": f"Streaming error: {str(e)}"},
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        return

    # Send final SSE events
    # 关闭推理块（如果有）
    if reasoning_started and reasoning_block_index is not None:
        yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': reasoning_block_index}, ensure_ascii=False)}\n\n"

    # 关闭文本块
    yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': text_block_index}, ensure_ascii=False)}\n\n"

    # 关闭工具调用块
    for tool_data in current_tool_calls.values():
        if tool_data.get("started") and tool_data.get("claude_index") is not None:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': tool_data['claude_index']}, ensure_ascii=False)}\n\n"

    usage_data = {"input_tokens": 0, "output_tokens": 0}
    yield f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_DELTA, 'delta': {'stop_reason': final_stop_reason, 'stop_sequence': None}, 'usage': usage_data}, ensure_ascii=False)}\n\n"
    yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_STOP}, ensure_ascii=False)}\n\n"


async def convert_openai_streaming_to_claude_with_cancellation(
    openai_stream,
    original_request: ClaudeMessagesRequest,
    logger,
    http_request: Request,
    openai_client,
    request_id: str,
):
    """Convert OpenAI streaming response to Claude streaming format with cancellation support."""

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    # Send initial SSE events
    yield f"event: {Constants.EVENT_MESSAGE_START}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_START, 'message': {'id': message_id, 'type': 'message', 'role': Constants.ROLE_ASSISTANT, 'model': original_request.model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': 0, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_PING}\ndata: {json.dumps({'type': Constants.EVENT_PING}, ensure_ascii=False)}\n\n"

    # Process streaming chunks
    text_block_index = 0
    reasoning_block_index = None  # 推理内容块的索引
    tool_block_counter = 0
    current_tool_calls = {}
    final_stop_reason = Constants.STOP_END_TURN
    usage_data = {"input_tokens": 0, "output_tokens": 0}
    reasoning_started = False  # 标记推理内容是否已开始
    text_block_started = False  # 标记文本块是否已开始（在推理块之后）

    try:
        async for line in openai_stream:
            # Check if client disconnected
            if await http_request.is_disconnected():
                logger.info(f"Client disconnected, cancelling request {request_id}")
                openai_client.cancel_request(request_id)
                break

            if line.strip():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(chunk_data)
                        # logger.info(f"OpenAI chunk: {chunk}")
                        usage = chunk.get("usage", None)
                        if usage:
                            cache_read_input_tokens = 0
                            prompt_tokens_details = usage.get('prompt_tokens_details', {})
                            if prompt_tokens_details:
                                cache_read_input_tokens = prompt_tokens_details.get('cached_tokens', 0)
                            usage_data = {
                                'input_tokens': usage.get('prompt_tokens', 0),
                                'output_tokens': usage.get('completion_tokens', 0),
                                'cache_read_input_tokens': cache_read_input_tokens
                            }
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"Failed to parse chunk: {chunk_data}, error: {e}"
                        )
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason")

                    # Handle reasoning content delta (专门针对 moonshotai/kimi-k2.6 模型)
                    # 将推理内容转换为 Claude 的 thinking 块
                    if delta and "reasoning_content" in delta and delta["reasoning_content"] is not None:
                        # 如果是第一个推理内容块，发送 content_block_start 事件
                        if not reasoning_started:
                            reasoning_block_index = text_block_index
                            # 发送 thinking 块的开始事件
                            yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': reasoning_block_index, 'content_block': {'type': Constants.CONTENT_THINKING, 'thinking': ''}}, ensure_ascii=False)}\n\n"
                            reasoning_started = True

                        # 发送推理内容增量
                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': reasoning_block_index, 'delta': {'type': Constants.DELTA_THINKING, 'thinking': delta['reasoning_content']}}, ensure_ascii=False)}\n\n"

                    # Handle text delta
                    if delta and "content" in delta and delta["content"] is not None:
                        # 确定当前文本块的索引
                        current_text_index = text_block_index
                        if reasoning_started:
                            # 如果之前有推理内容，文本块索引需要递增
                            current_text_index = reasoning_block_index + 1
                            # 如果这是第一次收到普通文本，需要发送新的文本块开始事件
                            if not text_block_started:
                                # 先关闭推理块
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': reasoning_block_index}, ensure_ascii=False)}\n\n"
                                # 开始文本块
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': current_text_index, 'content_block': {'type': Constants.CONTENT_TEXT, 'text': ''}}, ensure_ascii=False)}\n\n"
                                text_block_started = True
                                text_block_index = current_text_index

                        yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': current_text_index, 'delta': {'type': Constants.DELTA_TEXT, 'text': delta['content']}}, ensure_ascii=False)}\n\n"

                    # Handle tool call deltas with improved incremental processing
                    if "tool_calls" in delta and delta["tool_calls"]:
                        for tc_delta in delta["tool_calls"]:
                            tc_index = tc_delta.get("index", 0)
                            
                            # Initialize tool call tracking by index if not exists
                            if tc_index not in current_tool_calls:
                                current_tool_calls[tc_index] = {
                                    "id": None,
                                    "name": None,
                                    "args_buffer": "",
                                    "json_sent": False,
                                    "claude_index": None,
                                    "started": False
                                }
                            
                            tool_call = current_tool_calls[tc_index]
                            
                            # Update tool call ID if provided
                            if tc_delta.get("id"):
                                tool_call["id"] = tc_delta["id"]
                            
                            # Update function name and start content block if we have both id and name
                            function_data = tc_delta.get(Constants.TOOL_FUNCTION, {})
                            if function_data.get("name"):
                                tool_call["name"] = function_data["name"]
                            
                            # Start content block when we have complete initial data
                            if (tool_call["id"] and tool_call["name"] and not tool_call["started"]):
                                tool_block_counter += 1
                                claude_index = text_block_index + tool_block_counter
                                tool_call["claude_index"] = claude_index
                                tool_call["started"] = True
                                
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_START}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_START, 'index': claude_index, 'content_block': {'type': Constants.CONTENT_TOOL_USE, 'id': tool_call['id'], 'name': tool_call['name'], 'input': {}}}, ensure_ascii=False)}\n\n"
                            
                            # Handle function arguments — forward each fragment immediately
                            if "arguments" in function_data and tool_call["started"] and function_data["arguments"] is not None:
                                args_chunk = function_data["arguments"]
                                tool_call["args_buffer"] += args_chunk
                                yield f"event: {Constants.EVENT_CONTENT_BLOCK_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_DELTA, 'index': tool_call['claude_index'], 'delta': {'type': Constants.DELTA_INPUT_JSON, 'partial_json': args_chunk}}, ensure_ascii=False)}\n\n"

                    # Handle finish reason
                    if finish_reason:
                        if finish_reason == "length":
                            final_stop_reason = Constants.STOP_MAX_TOKENS
                        elif finish_reason in ["tool_calls", "function_call"]:
                            final_stop_reason = Constants.STOP_TOOL_USE
                        elif finish_reason == "stop":
                            final_stop_reason = Constants.STOP_END_TURN
                        else:
                            final_stop_reason = Constants.STOP_END_TURN

    except HTTPException as e:
        # Handle cancellation
        if e.status_code == 499:
            logger.info(f"Request {request_id} was cancelled")
            error_event = {
                "type": "error",
                "error": {
                    "type": "cancelled",
                    "message": "Request was cancelled by client",
                },
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            return
        else:
            # 流式传输已经开始，不能再 raise HTTPException，否则会导致
            # "Caught handled exception, but response already started" 错误
            # 将错误转为 SSE error 事件发送给客户端
            logger.error(f"HTTPException during streaming: status={e.status_code}, detail={e.detail}\n{traceback.format_exc()}")
            error_event = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Streaming error (HTTP {e.status_code}): {e.detail}",
                },
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            return
    except Exception as e:
        # Handle any streaming errors gracefully
        logger.error(f"Streaming error: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        error_event = {
            "type": "error",
            "error": {"type": "api_error", "message": f"Streaming error: {str(e)}"},
        }
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        return

    # Send final SSE events
    # 关闭推理块（如果有）
    if reasoning_started and reasoning_block_index is not None:
        yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': reasoning_block_index}, ensure_ascii=False)}\n\n"

    # 关闭文本块
    yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': text_block_index}, ensure_ascii=False)}\n\n"

    # 关闭工具调用块
    for tool_data in current_tool_calls.values():
        if tool_data.get("started") and tool_data.get("claude_index") is not None:
            yield f"event: {Constants.EVENT_CONTENT_BLOCK_STOP}\ndata: {json.dumps({'type': Constants.EVENT_CONTENT_BLOCK_STOP, 'index': tool_data['claude_index']}, ensure_ascii=False)}\n\n"

    yield f"event: {Constants.EVENT_MESSAGE_DELTA}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_DELTA, 'delta': {'stop_reason': final_stop_reason, 'stop_sequence': None}, 'usage': usage_data}, ensure_ascii=False)}\n\n"
    yield f"event: {Constants.EVENT_MESSAGE_STOP}\ndata: {json.dumps({'type': Constants.EVENT_MESSAGE_STOP}, ensure_ascii=False)}\n\n"
