"""Tavily-backed web search tool."""

from __future__ import annotations

import os

import requests
from langchain_core.tools import tool

_TAVILY_URL = "https://api.tavily.com/search"
_DEFAULT_MAX_RESULTS = 5
_MAX_MAX_RESULTS = 10
_TIMEOUT_SECONDS = 15
_MAX_QUERY_CHARS = 500


def _coerce_max_results(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS
    return max(1, min(_MAX_MAX_RESULTS, number))


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索实时信息，返回标题、链接和摘要。

    query 是搜索词，max_results 默认 5，范围 1-10。
    """
    text = (query or "").strip()
    if not text:
        return "[错误] 搜索词不能为空。"
    if len(text) > _MAX_QUERY_CHARS:
        return f"[错误] 搜索词过长（最多 {_MAX_QUERY_CHARS} 字符）。"

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return "[错误] 未配置 TAVILY_API_KEY，无法联网搜索。"

    count = _coerce_max_results(max_results)
    payload = {
        "query": text,
        "search_depth": "basic",
        "max_results": count,
        "include_answer": False,
        "include_raw_content": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _TAVILY_URL,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        return f"[错误] 搜索超时（{_TIMEOUT_SECONDS} 秒），请稍后重试。"
    except requests.exceptions.RequestException as exc:
        message = str(exc).strip()[:200] or "网络错误"
        return f"[错误] 搜索请求失败: {message}"

    if response.status_code >= 400:
        return f"[错误] 搜索请求失败（HTTP {response.status_code}）。"

    try:
        data = response.json()
    except ValueError:
        return "[错误] 搜索返回格式异常。"

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return "[错误] 搜索返回格式异常。"
    if not results:
        return "[搜索结果] 未找到相关内容。"

    lines = [f"[搜索结果] {text}"]
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            return "[错误] 搜索返回格式异常。"
        title = str(item.get("title") or "").strip() or "(无标题)"
        url = str(item.get("url") or "").strip() or "(无链接)"
        snippet = " ".join(str(item.get("content") or "").split())
        if not snippet:
            snippet = "(无摘要)"
        lines.extend(
            [
                f"{index}. {title}",
                f"   URL: {url}",
                f"   摘要: {snippet}",
            ]
        )
    return "\n".join(lines)
