"""
Web search service for claude-code-proxy.

Intercepts web_search built-in tool calls and executes real web searches
using DuckDuckGo (no API key required).
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


# Tool definition that will be injected into OpenAI requests
WEB_SEARCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information. Returns a list of search results with titles, URLs, and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to execute",
                }
            },
            "required": ["query"],
        },
    },
}


async def execute_web_search(query: str, max_results: int = 10) -> Dict[str, Any]:
    """
    Execute a web search using DuckDuckGo HTML scraping (no API key needed).

    Args:
        query: The search query
        max_results: Maximum number of results to return

    Returns:
        A dict with status and results list
    """
    try:
        results = await _duckduckgo_search(query, max_results)
        if results:
            return {"status": "success", "results": results}

        logger.warning(f"No results found for query: {query}")
        return {
            "status": "success",
            "results": [
                {
                    "title": "No results found",
                    "url": "",
                    "snippet": f'No search results found for "{query}". Try a different query.',
                }
            ],
        }

    except Exception as e:
        logger.error(f"Web search failed for query '{query}': {e}")
        return {
            "status": "error",
            "results": [
                {
                    "title": "Search Error",
                    "url": "",
                    "snippet": f"Web search failed: {str(e)}. The search service may be temporarily unavailable.",
                }
            ],
        }


async def _duckduckgo_search(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Search using DuckDuckGo's HTML interface (no API key required).

    Parses links, titles, and snippets from the HTML result page using
    a straightforward approach: extract all links, URLs, and snippets
    separately, then zip them together by position.
    """
    import httpx
    from urllib.parse import quote_plus
    import re
    import html

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        page_html = response.text

    # Extract all title links: <a class="result__a" href="...">title</a>
    title_links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        page_html,
        re.DOTALL,
    )

    # Extract all snippets: <a class="result__snippet" ...>snippet</a>
    raw_snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        page_html,
        re.DOTALL,
    )

    results = []
    for i, (href, title_html) in enumerate(title_links[:max_results]):
        title = _clean_html(title_html)
        snippet = _clean_html(raw_snippets[i]) if i < len(raw_snippets) else ""
        actual_url = _extract_ddg_url(href)

        if title:
            results.append(
                {
                    "title": html.unescape(title),
                    "url": actual_url,
                    "snippet": html.unescape(snippet),
                }
            )

    return results


def _clean_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    import re
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_ddg_url(href: str) -> str:
    """Extract the actual URL from DuckDuckGo's redirect URL."""
    from urllib.parse import unquote, urlparse, parse_qs

    if "duckduckgo.com" in href and "uddg=" in href:
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])
    return href
