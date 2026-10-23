# Web Search Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `web_search` LangChain tool that queries Tavily and returns titles, URLs, and snippets to the agent.

**Architecture:** A new `web_search_tool.py` owns the Tavily HTTP call, validation, limits, and formatting. `graph_agent.py` only imports the tool, registers it in `TOOLS`, and tells the model when to use it. Tests mock `requests.post`; one manual smoke uses the real API.

**Tech Stack:** Python 3.12, `requests` (already installed in `rag`), `langchain-core` `@tool`, `unittest`.

---

## File Structure

- Create: `D:\Vibecoding\Langgraph\web_search_tool.py` — Tavily request and formatting.
- Create: `D:\Vibecoding\Langgraph\tests\test_web_search.py` — mocked unit tests.
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py` — import, system prompt, `TOOLS`.
- Create: `D:\Vibecoding\Langgraph\docs\superpowers\plans\2026-10-23-web-search-tool.md` — this plan.
- Existing spec: `D:\Vibecoding\Langgraph\docs\superpowers\specs\2026-10-23-web-search-tool-design.md`.

---

### Task 1: `web_search_tool.py` and its tests

**Files:**
- Create: `D:\Vibecoding\Langgraph\web_search_tool.py`
- Test: `D:\Vibecoding\Langgraph\tests\test_web_search.py`

- [ ] **Step 1: Write the failing tests**

Create `D:\Vibecoding\Langgraph\tests\test_web_search.py`:

```python
import os
import unittest
from unittest.mock import Mock, patch

import requests

from web_search_tool import web_search


class WebSearchToolTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "test-key"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _response(self, payload, status=200):
        response = Mock()
        response.status_code = status
        response.json.return_value = payload
        return response

    @patch("web_search_tool.requests.post")
    def test_formats_results(self, post):
        post.return_value = self._response(
            {
                "results": [
                    {
                        "title": "量子计算新进展",
                        "url": "https://example.com/quantum",
                        "content": "研究人员公布了新的纠错方案。",
                    },
                    {
                        "title": "行业观察",
                        "url": "https://example.com/report",
                        "content": "未来五年市场规模预测。",
                    },
                ]
            }
        )

        result = web_search.invoke(
            {"query": "量子计算", "max_results": 2}
        )

        self.assertIn("[搜索结果] 量子计算", result)
        self.assertIn("1. 量子计算新进展", result)
        self.assertIn("URL: https://example.com/quantum", result)
        self.assertIn("摘要: 研究人员公布了新的纠错方案。", result)
        self.assertIn("2. 行业观察", result)
        self.assertNotIn("test-key", result)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["query"], "量子计算")
        self.assertEqual(sent["max_results"], 2)
        self.assertEqual(sent["search_depth"], "basic")
        self.assertFalse(sent["include_answer"])
        self.assertFalse(sent["include_raw_content"])
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    @patch("web_search_tool.requests.post")
    def test_clamps_max_results(self, post):
        post.return_value = self._response({"results": []})

        web_search.invoke({"query": "a", "max_results": 99})
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 10)

        web_search.invoke({"query": "b", "max_results": 0})
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 1)

    @patch("web_search_tool.requests.post")
    def test_invalid_max_results_uses_default(self, post):
        post.return_value = self._response({"results": []})

        web_search.func("abc", "not-a-number")

        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 5)

    @patch("web_search_tool.requests.post")
    def test_empty_results(self, post):
        post.return_value = self._response({"results": []})

        result = web_search.invoke({"query": "没有结果"})

        self.assertEqual(result, "[搜索结果] 未找到相关内容。")

    @patch("web_search_tool.requests.post")
    def test_missing_api_key(self, post):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            result = web_search.invoke({"query": "测试"})

        self.assertIn("未配置 TAVILY_API_KEY", result)
        post.assert_not_called()

    @patch("web_search_tool.requests.post")
    def test_timeout(self, post):
        post.side_effect = requests.exceptions.Timeout()

        result = web_search.invoke({"query": "测试"})

        self.assertIn("搜索超时", result)

    @patch("web_search_tool.requests.post")
    def test_http_error(self, post):
        post.return_value = self._response({}, status=500)

        result = web_search.invoke({"query": "测试"})

        self.assertEqual(result, "[错误] 搜索请求失败（HTTP 500）。")

    @patch("web_search_tool.requests.post")
    def test_malformed_response(self, post):
        post.return_value = self._response({"results": "not-a-list"})

        result = web_search.invoke({"query": "测试"})

        self.assertEqual(result, "[错误] 搜索返回格式异常。")

    @patch("web_search_tool.requests.post")
    def test_empty_query(self, post):
        result = web_search.invoke({"query": "   "})

        self.assertEqual(result, "[错误] 搜索词不能为空。")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_web_search
```

Expected: FAIL with `ModuleNotFoundError: No module named 'web_search_tool'`.

- [ ] **Step 3: Write the minimal implementation**

Create `D:\Vibecoding\Langgraph\web_search_tool.py`:

```python
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
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_web_search
```

Expected: `Ran 9 tests` — `OK`.

- [ ] **Step 5: Commit Task 1**

```bash
git add Langgraph/web_search_tool.py Langgraph/tests/test_web_search.py
git commit -m "feat: add Tavily web search tool"
```

---

### Task 2: Register the tool in `graph_agent.py`

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py:49-57`
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py:97-108`
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py:719-727`

- [ ] **Step 1: Add the import**

After the existing `model_registry` import block, add:

```python
from web_search_tool import web_search
```

The top of the file should now contain, in order:

```python
from model_registry import (
    ModelConfigError,
    ModelProfile,
    build_chat_model,
    get_profile,
    initial_model_key,
    model_table,
    resolve_selector,
)
from web_search_tool import web_search
```

- [ ] **Step 2: Add the system-prompt instruction**

Change the first sentence of `SYSTEM_PROMPT` to:

```python
SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助手。需要实时信息、新闻或网上资料时，"
    "用 web_search 搜索，不要凭记忆编造最新事实；搜索结果不足时如实说明。"
    "读取文件或目录用 read_file，大文件可用 offset/max_chars 分页；"
    "CSV 用 read_csv，JSON 用 read_json。"
    "文本文件新建/覆盖用 write_file，可用 create_parents 创建父目录、encoding 指定编码；"
    "局部修改用 edit_file。"
    "Word 用 read_docx/write_docx/edit_docx；Excel 用 read_xlsx/write_xlsx/edit_xlsx；"
    "PPT 用 read_pptx/write_pptx/edit_pptx；PDF 只读用 read_pdf。"
    "修改已有办公文件前先用对应的 read_* 工具确认原文；"
    "写入或修改完成后，只报告文件路径和完成状态，不要复述写入或修改的内容；"
    "路径优先使用绝对路径；工具返回错误时如实说明，不要猜测文件内容。"
)
```

- [ ] **Step 3: Add the tool to `TOOLS`**

Change `TOOLS` to:

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

- [ ] **Step 4: Verify registration and the full suite**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -c "from graph_agent import TOOLS; print([t.name for t in TOOLS])"
```

Expected output includes:

```text
['get_current_time', 'read_file', 'read_csv', 'read_json', 'write_file', 'edit_file', 'web_search', 'read_docx', ...]
```

Then run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v
```

Expected: `Ran 151 tests` — `OK`.

- [ ] **Step 5: Commit Task 2**

```bash
git add Langgraph/graph_agent.py
git commit -m "feat: register web search tool"
```

---

### Task 3: Real Tavily smoke and final plan commit

**Files:**
- Modify: `D:\Vibecoding\Langgraph\docs\superpowers\plans\2026-10-23-web-search-tool.md` (checkboxes)
- No code changes expected.

- [ ] **Step 1: Run one real Tavily search**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -c "from web_search_tool import web_search; print(web_search.invoke({'query': '2026年10月23日 科技新闻', 'max_results': 3}))"
```

Expected: three numbered results with `URL:` lines and snippets; no API key in output.

If the API returns HTTP 401, change the request to Tavily's body-key form:

```python
payload["api_key"] = api_key
```

and keep the `Authorization` header; rerun the smoke. Do not print the key.

- [ ] **Step 2: Run the full suite again**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v
```

Expected: `Ran 151 tests` — `OK`.

- [ ] **Step 3: Start the agent and confirm the model uses the tool**

Run:

```bash
$env:LLM_PROVIDER="ollama"; D:\Anaconda3\envs\rag\python.exe graph_agent.py
```

At the prompt:

```text
你: 用 web_search 查一下今天有哪些科技新闻，列三条并给链接
你: exit
```

Expected: a `[工具] web_search(...)` line, then an answer with titles/URLs from the tool output.

- [ ] **Step 4: Mark this plan's checkboxes complete and commit**

Change every completed `- [ ] **Step` to `- [x] **Step` in this plan, then:

```bash
git add Langgraph/docs/superpowers/plans/2026-10-23-web-search-tool.md
git commit -m "docs: mark web search tool plan complete"
```

---

## Self-Review

- [x] **Spec coverage:** The spec's tool signature, Tavily request, formatting, 1–10 clamp, error strings, prompt integration, and tests map to Tasks 1–3.
- [x] **Placeholder scan:** No `TBD`, `TODO`, `FIXME`, "implement later", or "add appropriate" remains. The 401 fallback is a concrete code change.
- [x] **Type consistency:** `web_search(query: str, max_results: int = 5) -> str` is used by the tests, `TOOLS`, and the manual smoke. `_coerce_max_results` accepts any value and returns 1–10.
- [x] **Spec mismatch check:** No raw content, generated answer, cache, or filters are added. The API key is read at call time.
- [x] **Test count check:** 142 existing + 9 new = 151.
