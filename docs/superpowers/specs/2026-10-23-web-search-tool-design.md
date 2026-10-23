# Web Search Tool Design

## Background

The agent currently has local file tools, Office tools, session commands, subagents, and runtime model switching. It has no way to look up current information online.

`.env` already contains `TAVILY_API_KEY`, and the `rag` environment already has `requests` installed. This feature adds a `web_search` tool that queries Tavily and returns a compact, model-readable result list.

The user approved:

- Tavily as the search provider.
- Default 5 results, adjustable from 1 to 10.
- Titles, URLs, and snippets only; no full page bodies.

## Goals

1. Add a LangChain tool named `web_search` with signature:

   ```python
   web_search(query: str, max_results: int = 5) -> str
   ```

2. Query Tavily's REST search endpoint with a short timeout.
3. Return a numbered list of title, URL, and snippet.
4. Default to 5 results; clamp `max_results` to 1–10.
5. Return clear `[错误] ...` strings for missing key, timeout, HTTP failure, malformed response, and empty query.
6. Return `[搜索结果] 未找到相关内容。` when Tavily returns no results.
7. Expose the tool to the main agent and to subagents (it is part of the core `TOOLS` list).
8. Never print or log the API key or the raw Tavily response.

## Non-Goals

- No raw page content, page fetching, or browser automation.
- No Tavily `include_answer` generated answer.
- No result caching.
- No domain, date, language, or news-topic filters.
- No retry/backoff beyond one request.
- No change to the model-switching, session, or subagent behavior.
- No new Python package; use the already installed `requests`.

## Component

New file: `web_search_tool.py`.

```python
from langchain_core.tools import tool

@tool
def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索实时信息，返回标题、链接和摘要。"""
```

The function is the only public export. It reads `TAVILY_API_KEY` at call time, so tests and runtime can change the environment without reloading the module.

## Tavily Request

Endpoint:

```text
POST https://api.tavily.com/search
```

Headers:

```text
Authorization: Bearer <TAVILY_API_KEY>
Content-Type: application/json
```

JSON body:

```json
{
  "query": "<user query>",
  "search_depth": "basic",
  "max_results": 5,
  "include_answer": false,
  "include_raw_content": false
}
```

Timeout: 15 seconds.

The exact auth form will be confirmed during implementation with one real request. If Tavily rejects the Bearer header, use its documented `api_key` body field as the fallback and keep the key out of output.

## Output Format

```text
[搜索结果] 查询词
1. 标题
   URL: https://example.com/a
   摘要: ...
2. 标题
   URL: https://example.com/b
   摘要: ...
```

Rules:

- Query is echoed once after `[搜索结果]`.
- Number results starting at 1.
- Missing title: use `(无标题)`.
- Missing URL: use `(无链接)`.
- Missing snippet: use `(无摘要)`.
- If `results` is empty, return `[搜索结果] 未找到相关内容。`
- If `results` is not a list or an item is not a dict, return `[错误] 搜索返回格式异常。`

## Validation and Limits

- `query`: strip surrounding whitespace; empty → `[错误] 搜索词不能为空。`
- `query` longer than 500 characters → `[错误] 搜索词过长（最多 500 字符）。`
- `max_results`: coerce to int when possible; invalid → use 5; clamp to 1–10.
- API key missing → `[错误] 未配置 TAVILY_API_KEY，无法联网搜索。`
- Timeout → `[错误] 搜索超时（15 秒），请稍后重试。`
- HTTP error → `[错误] 搜索请求失败（HTTP <status>）。`
- JSON decode error → `[错误] 搜索返回格式异常。`
- Other request errors → `[错误] 搜索请求失败: <short message>`.

Do not include the API key, request headers, or full response body in any error string.

## Integration

In `graph_agent.py`:

```python
from web_search_tool import web_search
```

Add `web_search` to `TOOLS`:

```python
TOOLS = [
    get_current_time,
    read_file,
    read_csv,
    read_json,
    write_file,
    edit_file,
    web_search,
    *OFFICE_TOOLS,
]
```

Append to `SYSTEM_PROMPT`:

```text
需要实时信息、新闻或网上资料时，用 web_search 搜索；
不要凭记忆编造最新事实，搜索结果不足时如实说明。
```

`build_graph()` already binds `TOOLS`, and subagent workers call it with `include_subagent_tools=False`; `web_search` is a core tool, so both main and subagents can use it.

## Tests

New file: `tests/test_web_search.py`.

Mock `requests.post`; no real network in unit tests.

| Test | Assertion |
|---|---|
| `test_formats_results` | numbered title/URL/snippet; query echoed |
| `test_clamps_max_results` | `max_results=99` sends 10 and `0` sends 1 |
| `test_invalid_max_results_uses_default` | `"abc"` sends 5 |
| `test_empty_results` | returns `未找到相关内容` |
| `test_missing_api_key` | returns `未配置 TAVILY_API_KEY`; no request |
| `test_timeout` | returns `搜索超时` |
| `test_http_error` | returns `HTTP 500` |
| `test_malformed_response` | missing/non-list `results` returns format error |
| `test_empty_query` | returns `搜索词不能为空` |

Expected new test count: 9. Existing suite is 142; expected total 151.

## Manual Verification

After unit tests pass:

1. Run one real Tavily search with `max_results=3` for a current-topic query.
2. Confirm the output has three numbered results with URLs and snippets.
3. Confirm no API key appears in output.
4. Start `graph_agent.py`, ask a current-information question, and confirm the model calls `web_search` and cites returned URLs.

The real request is a network operation; it is not part of the unit test suite.

## Risks

- Tavily API auth or response shape may have changed; the implementation's single real request will confirm it.
- Search results can be wrong or stale; the tool returns sources and the system prompt tells the model to be honest when results are insufficient.
- A 15-second timeout can fail on slow networks; the error is shown to the user and the agent can retry.
