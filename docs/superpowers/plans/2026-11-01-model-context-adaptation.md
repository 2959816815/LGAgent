# Model Context Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the full transcript while building a per-model context view, maintain a rolling summary while online, and automatically fall back to a local 16k-window model with core tools when the cloud is unreachable.

**Architecture:** `context_builder.py` derives a model-specific view (system prompt + rolling summary + token-budgeted recent turns) without touching stored messages. `desktop_api.py` integrates the builder, runs one cloud attempt, and on a network-class failure switches the session to the local model and retries once. The local profile uses a core tool subset and a derived Ollama model with `num_ctx=16384`.

**Tech Stack:** Python 3.12 (`D:\Anaconda3\envs\rag\python.exe`), FastAPI, LangChain/LangGraph, Ollama 0.18, React 18, TypeScript, Vitest.

**Scope:** Development tree only. Do not build or install the desktop package, and do not change the installed application's files, Ollama model name, or server-wide Ollama environment variables.

**Spec:** `docs/superpowers/specs/2026-11-01-model-context-adaptation-design.md`

---

### Task 1: Session summary coverage fields

**Files:**
- Modify: `Langgraph/session_store.py:40-51`, `:217-286`
- Modify: `Langgraph/session_store_sqlite.py:45-78`, `:116-129`, `:204-322`
- Test: `Langgraph/tests/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_store.py` (imports at the top: `import sqlite3`, `from session_store_sqlite import SqliteSessionStore`):

```python
class SummaryCoverageStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_summary_cover_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_json_store_roundtrips_summary_coverage(self):
        store = SessionStore(self.root / ".sessions")
        session = store.create("覆盖")
        session.summary = "旧摘要"
        session.summary_upto = 3
        session.summary_updated_at = "2026-11-01T00:00:00+08:00"
        store.save(session)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 3)
        self.assertEqual(loaded.summary_updated_at, "2026-11-01T00:00:00+08:00")
        self.assertEqual(loaded.summary_count, 0)

    def test_old_json_session_defaults_coverage_to_zero(self):
        store = SessionStore(self.root / ".sessions")
        session = store.create("旧会话")
        path = self.root / ".sessions" / f"{session.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("summary_upto", None)
        raw.pop("summary_updated_at", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 0)
        self.assertEqual(loaded.summary_updated_at, "")

    def test_sqlite_store_roundtrips_summary_coverage(self):
        store = SqliteSessionStore(self.root / "agent.db")
        session = store.create("覆盖")
        session.summary = "旧摘要"
        session.summary_upto = 5
        session.summary_updated_at = "2026-11-01T00:00:00+08:00"
        store.save(session)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 5)
        self.assertEqual(loaded.summary_updated_at, "2026-11-01T00:00:00+08:00")

    def test_sqlite_adds_columns_to_old_database(self):
        db = self.root / "old.db"
        connection = sqlite3.connect(db)
        connection.execute(
            "CREATE TABLE sessions ("
            "id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, model_key TEXT, "
            "message_count INTEGER NOT NULL DEFAULT 0, "
            "summary TEXT NOT NULL DEFAULT '', "
            "summary_count INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES('20260101-000000-abcd', '旧', "
            "'2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00', "
            "'local', 0, '', 0)"
        )
        connection.commit()
        connection.close()

        store = SqliteSessionStore(db)
        loaded = store.load("20260101-000000-abcd")

        self.assertEqual(loaded.summary_upto, 0)
        self.assertEqual(loaded.summary_updated_at, "")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_session_store.SummaryCoverageStoreTests
```

Expected: FAIL with `TypeError: Session.__init__() got an unexpected keyword argument 'summary_upto'` or an `AttributeError`.

- [ ] **Step 3: Implement the fields and stores**

In `session_store.py`, add to `Session` after `summary_count`:

```python
    # 摘要覆盖到的消息条数：summary 描述 messages[:summary_upto]
    summary_upto: int = 0
    summary_updated_at: str = ""
```

In `SessionStore.save`, add to the payload:

```python
            "summary_upto": session.summary_upto,
            "summary_updated_at": session.summary_updated_at,
```

In `SessionStore.load`, after the `summary_count` parsing, add:

```python
        summary_upto = raw.get("summary_upto") or 0
        if isinstance(summary_upto, bool) or not isinstance(summary_upto, int):
            summary_upto = 0
        summary_upto = max(0, summary_upto)
        summary_updated_at = raw.get("summary_updated_at") or ""
        if not isinstance(summary_updated_at, str):
            summary_updated_at = ""
```

Pass them to the returned `Session`:

```python
        return Session(
            session_id,
            title,
            created_at,
            updated_at,
            messages,
            model_key,
            summary=summary,
            summary_count=summary_count,
            summary_upto=summary_upto,
            summary_updated_at=summary_updated_at,
        )
```

In `session_store_sqlite.py`, add the columns to `_SCHEMA`:

```sql
    summary       TEXT NOT NULL DEFAULT '',
    summary_count INTEGER NOT NULL DEFAULT 0,
    summary_upto  INTEGER NOT NULL DEFAULT 0,
    summary_updated_at TEXT NOT NULL DEFAULT ''
```

In `_ensure_summary_columns`, add:

```python
        if "summary_upto" not in columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN summary_upto INTEGER NOT NULL DEFAULT 0"
            )
        if "summary_updated_at" not in columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN summary_updated_at TEXT NOT NULL DEFAULT ''"
            )
```

In `_insert_session`, extend the insert and the argument tuple:

```python
            connection.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at, model_key, "
                "message_count, summary, summary_count, summary_upto, summary_updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, created_at=excluded.created_at, "
                "updated_at=excluded.updated_at, model_key=excluded.model_key, "
                "message_count=excluded.message_count, summary=excluded.summary, "
                "summary_count=excluded.summary_count, summary_upto=excluded.summary_upto, "
                "summary_updated_at=excluded.summary_updated_at",
                (
                    session.id,
                    session.title,
                    session.created_at,
                    session.updated_at,
                    session.model_key,
                    len(messages),
                    session.summary,
                    session.summary_count,
                    session.summary_upto,
                    session.summary_updated_at,
                ),
            )
```

In `SqliteSessionStore.load`, select and map the new columns:

```python
                "SELECT id, title, created_at, updated_at, model_key, summary, "
                "summary_count, summary_upto, summary_updated_at "
                "FROM sessions WHERE id = ?",
```

```python
            summary_count=int(row[6] or 0),
            summary_upto=int(row[7] or 0),
            summary_updated_at=row[8] or "",
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_session_store
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add session_store.py session_store_sqlite.py tests/test_session_store.py
git commit -m "feat: store summary coverage without deleting messages"
```

---

### Task 2: Token estimation, budgets, and text truncation

**Files:**
- Create: `Langgraph/context_builder.py`
- Test: `Langgraph/tests/test_context_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_context_builder.py`:

```python
from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage

from context_builder import (
    ContextBudgetError,
    TRUNCATE_MARKER,
    compute_budget,
    estimate_text_tokens,
    estimate_tokens,
    truncate_to_tokens,
)


class TokenEstimateTests(unittest.TestCase):
    def test_chinese_is_about_one_token_per_character(self):
        tokens = estimate_text_tokens("字" * 400)

        self.assertGreaterEqual(tokens, 400)
        self.assertLessEqual(tokens, 500)

    def test_ascii_is_about_four_chars_per_token(self):
        tokens = estimate_text_tokens("a" * 400)

        self.assertGreaterEqual(tokens, 100)
        self.assertLessEqual(tokens, 130)

    def test_message_overhead_is_counted(self):
        tokens = estimate_tokens([HumanMessage(content="hi")])

        self.assertGreater(tokens, 0)

    def test_images_are_counted(self):
        text_only = estimate_tokens([HumanMessage(content="字" * 100)])
        with_image = estimate_tokens(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": "字" * 100},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AA"},
                        },
                    ]
                )
            ]
        )

        self.assertGreaterEqual(with_image - text_only, 1000)


class BudgetTests(unittest.TestCase):
    def test_16k_local_budget_leaves_room_for_conversation(self):
        budget = compute_budget(16384, 620, 3200)

        self.assertGreater(budget["conversation"], 7000)
        self.assertLess(budget["conversation"], 10000)
        self.assertEqual(budget["max_message"], 3387)

    def test_tiny_window_raises(self):
        with self.assertRaises(ContextBudgetError):
            compute_budget(4096, 620, 5700)


class TruncateTests(unittest.TestCase):
    def test_truncation_respects_the_limit_and_marks_the_text(self):
        cut = truncate_to_tokens("字" * 1000, 100)

        self.assertLessEqual(estimate_text_tokens(cut), 100)
        self.assertTrue(cut.endswith(TRUNCATE_MARKER))

    def test_short_text_is_unchanged(self):
        self.assertEqual(truncate_to_tokens("短文本", 100), "短文本")
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder
```

Expected: FAIL with `ModuleNotFoundError: No module named 'context_builder'`.

- [ ] **Step 3: Create `context_builder.py` with estimation and budget code**

```python
"""Build model-specific context views and maintain rolling summaries.

The session transcript is append-only. This module never deletes messages; it
only derives the message list that is sent to a model.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from session_store import sanitize_messages

TOKEN_SAFETY = 1.15
MESSAGE_OVERHEAD = 4
IMAGE_TOKEN_ESTIMATE = 1000
MIN_KEEP_TURNS = 2
MAX_SUMMARY_CHUNK_TOKENS = 6000
MAX_SUMMARY_CALLS = 3
SUMMARY_PREFIX = "【较早对话的摘要】"
TRUNCATE_MARKER = "\n…[内容过长已截断]"
SUMMARY_TRUNCATE_MARKER = "…[摘要已截断]"

_CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)
_tool_token_cache: dict[tuple[str, ...], int] = {}


class ContextBudgetError(RuntimeError):
    """The model window cannot fit the system prompt and tools."""


class SummaryError(RuntimeError):
    """Summary generation failed."""


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _text_units(text: str) -> float:
    units = 0.0
    for char in str(text or ""):
        if _is_cjk(char):
            units += 1.0
        elif char.isascii() and (char.isalnum() or char.isspace()):
            units += 0.25
        else:
            units += 0.5
    return units


def estimate_text_tokens(text: str) -> int:
    return math.ceil(_text_units(text) * TOKEN_SAFETY)


def _content_text(content) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _image_count(message: BaseMessage) -> int:
    content = getattr(message, "content", "")
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}
    )


def estimate_tokens(messages: Iterable[BaseMessage]) -> int:
    units = 0.0
    for message in messages:
        units += _text_units(_content_text(getattr(message, "content", "")))
        units += _image_count(message) * IMAGE_TOKEN_ESTIMATE
        units += MESSAGE_OVERHEAD
    return math.ceil(units * TOKEN_SAFETY)


def truncate_to_tokens(
    text: str, limit: int, marker: str = TRUNCATE_MARKER
) -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    if estimate_text_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_text_tokens(text[:mid] + marker) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low] + marker


def compute_budget(window: int, system_tokens: int, tool_tokens: int) -> dict:
    output_reserve = max(512, min(int(window * 0.15), 4096))
    safety_margin = int(window * 0.10)
    conversation = window - system_tokens - tool_tokens - output_reserve - safety_margin
    if conversation <= 0:
        raise ContextBudgetError(
            f"上下文预算不足：窗口 {window}，系统提示 {system_tokens}，"
            f"工具 {tool_tokens}，输出预留 {output_reserve}，余量 {safety_margin}"
        )
    return {
        "window": window,
        "system_tokens": system_tokens,
        "tool_tokens": tool_tokens,
        "output_reserve": output_reserve,
        "safety_margin": safety_margin,
        "conversation": conversation,
        "summary_cap": max(400, min(int(conversation * 0.25), 2000)),
        "tail_target": int(conversation * 0.75),
        "max_message": max(512, int(conversation * 0.40)),
    }
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder
```

Expected: PASS. (`test_images_are_counted` may be off by the multiplier rounding; if it fails, adjust the assertion to `>= 1000`; the implementation already counts images before the 1.15 multiplier.)

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add context_builder.py tests/test_context_builder.py
git commit -m "feat: add CJK-aware token estimation and context budgets"
```

---

### Task 3: Core tool subset for local models

**Files:**
- Modify: `Langgraph/graph_agent.py:753-791`
- Modify: `Langgraph/model_registry.py:102`, `:141`
- Test: `Langgraph/tests/test_core_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_core_tools.py`:

```python
from __future__ import annotations

import unittest

from graph_agent import CORE_TOOL_NAMES, TOOLS, collect_tools


class CoreToolTests(unittest.TestCase):
    def test_local_model_gets_only_core_tools(self):
        tools = collect_tools(model_key="local")
        names = {tool.name for tool in tools}

        self.assertEqual(names, set(CORE_TOOL_NAMES))
        self.assertIn("read_file", names)
        self.assertNotIn("read_docx", names)

    def test_cloud_model_gets_all_builtin_tools(self):
        tools = collect_tools(model_key="deepseek")
        names = {tool.name for tool in tools}

        self.assertEqual(names, {tool.name for tool in TOOLS})
        self.assertIn("read_docx", names)
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_core_tools
```

Expected: FAIL with `ImportError: cannot import name 'CORE_TOOL_NAMES'`.

- [ ] **Step 3: Implement the subset and the tool collector**

In `graph_agent.py`, replace `select_tools` and `build_graph` with:

```python
CORE_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "read_file",
        "write_file",
        "edit_file",
        "web_search",
        "web_fetch",
        "download_file",
        "read_csv",
        "read_json",
        "read_pdf",
    }
)


def select_tools(read_only: bool = False, core_only: bool = False) -> list[Any]:
    """返回内置工具；core_only 时只保留本地小模型可用的核心工具。"""
    tools = list(TOOLS)
    if core_only:
        tools = [
            tool for tool in tools
            if getattr(tool, "name", "") in CORE_TOOL_NAMES
        ]
    if read_only:
        tools = [
            tool for tool in tools
            if getattr(tool, "name", "") not in WRITE_TOOL_NAMES
        ]
    return tools


def collect_tools(
    manager=None,
    include_subagent_tools: bool = True,
    model_key: str | None = None,
    extra_tools: list[Any] | None = None,
    read_only: bool = False,
) -> list[Any]:
    """返回某个模型实际会绑定的工具列表（也供 token 预算测量）。"""
    try:
        profile = get_profile(model_key or initial_model_key())
    except ModelConfigError:
        profile = None
    core_only = profile is not None and profile.provider == "ollama"
    tools = select_tools(read_only, core_only=core_only)
    if not core_only:
        if include_subagent_tools and manager is not None:
            from subagent_tools import build_subagent_tools

            tools = tools + build_subagent_tools(manager)
        if extra_tools:
            tools = tools + list(extra_tools)
    tools = wrap_tools(tools)
    return wrap_mcp_tools(tools)


def build_graph(
    manager=None,
    include_subagent_tools: bool = True,
    model_key: str | None = None,
    extra_tools: list[Any] | None = None,
    read_only: bool = False,
):
    """构建并编译 Agent 图：agent -> (tools -> agent)* -> END。"""
    tools = collect_tools(
        manager,
        include_subagent_tools=include_subagent_tools,
        model_key=model_key,
        extra_tools=extra_tools,
        read_only=read_only,
    )
    llm_with_tools = build_llm(model_key).bind_tools(tools)

    def agent_node(state: MessagesState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()
```

In `model_registry.py`, change both local `context_window` defaults from `"4096"` to `"16384"`:

```python
                    context_window=int(_env("OLLAMA_CONTEXT_WINDOW", "16384")),
```

There are two occurrences: `_dynamic_profiles` and the built-in `local` profile.

- [ ] **Step 4: Run the test and verify it passes**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_core_tools
```

Expected: PASS.

- [ ] **Step 5: Keep the extra-tools test on a cloud profile**

`build_graph` now ignores extra tools for local profiles, so
`tests/test_graph_extra_tools.py` must request a cloud model:

```python
            graph_agent.build_graph(
                manager=None,
                include_subagent_tools=False,
                model_key="deepseek",
                extra_tools=[extra],
            )
```

Run:

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_model_registry tests.test_office_tools tests.test_graph_extra_tools tests.test_graph_model tests.test_subagent_write_policy
```

Expected: PASS. `test_office_tools` inspects the global `TOOLS` list directly and does not build a local graph, so the core subset does not affect it.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add graph_agent.py model_registry.py tests/test_core_tools.py tests/test_graph_extra_tools.py
git commit -m "feat: give local models a core tool subset and 16k window"
```

---

### Task 4: Context view builder

**Files:**
- Modify: `Langgraph/context_builder.py`
- Test: `Langgraph/tests/test_context_builder.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_builder.py`:

```python
from types import SimpleNamespace

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from context_builder import SUMMARY_PREFIX, build_view, measure_tool_tokens, split_turns


def profile(window=16384, vision=False):
    return SimpleNamespace(context_window=window, supports_vision=vision)


def session(messages, summary=""):
    return SimpleNamespace(messages=messages, summary=summary, summary_upto=0)


class ViewTests(unittest.TestCase):
    def test_view_keeps_the_newest_turn_and_drops_whole_old_turns(self):
        messages = []
        for index in range(40):
            factory = HumanMessage if index % 2 == 0 else AIMessage
            messages.append(factory(content="内容" * 400))

        view, stats = build_view(session(messages), profile(), "系统" * 100, 0)

        self.assertGreater(stats.dropped_turns, 0)
        self.assertLessEqual(stats.view_tokens, 16384)
        self.assertIsInstance(view[0], SystemMessage)
        self.assertIsInstance(view[-1], AIMessage)

    def test_tool_call_and_result_stay_together(self):
        messages = [
            HumanMessage(content="读文件"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "read_file", "args": {}}],
            ),
            ToolMessage(content="内容", tool_call_id="call-1", name="read_file"),
            AIMessage(content="完成"),
        ]

        view, _ = build_view(session(messages), profile(), "系统", 0)

        tool_index = next(
            index for index, message in enumerate(view) if isinstance(message, ToolMessage)
        )
        self.assertIsInstance(view[tool_index - 1], AIMessage)
        self.assertTrue(view[tool_index - 1].tool_calls)

    def test_summary_is_injected_and_capped(self):
        view, stats = build_view(
            session([HumanMessage(content="问题")], summary="摘要" * 5000),
            profile(window=4096),
            "系统",
            0,
        )

        self.assertIn(SUMMARY_PREFIX, str(view[1].content))
        self.assertGreater(stats.summary_tokens, 0)
        self.assertLessEqual(stats.summary_tokens, stats.window)

    def test_images_are_replaced_for_non_vision_models(self):
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "看图"},
                    {"type": "image_ref", "attachment_id": "a1"},
                ]
            )
        ]

        view, stats = build_view(session(messages), profile(vision=False), "系统", 0)

        self.assertEqual(stats.images_dropped, 1)
        self.assertIn("[图片已省略]", str(view[-1].content))

    def test_images_are_kept_for_vision_models(self):
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "看图"},
                    {"type": "image_ref", "attachment_id": "a1"},
                ]
            )
        ]

        view, stats = build_view(
            session(messages), profile(vision=True), "系统", 0,
            hydrate=lambda items: items,
        )

        self.assertEqual(stats.images_dropped, 0)
        self.assertIsInstance(view[-1].content, list)

    def test_oversized_newest_turn_is_truncated(self):
        messages = [HumanMessage(content="字" * 100000)]

        view, stats = build_view(session(messages), profile(), "系统", 0)

        self.assertIn("内容过长已截断", str(view[-1].content))
        self.assertLessEqual(stats.view_tokens, 16384)

    def test_split_turns_starts_a_new_turn_at_each_human_message(self):
        turns = split_turns(
            [
                HumanMessage(content="a"),
                AIMessage(content="b"),
                HumanMessage(content="c"),
                AIMessage(content="d"),
            ]
        )

        self.assertEqual(len(turns), 2)
        self.assertEqual(len(turns[0]), 2)


class ToolTokenTests(unittest.TestCase):
    def test_schema_size_is_measured_and_cached(self):
        class FakeTool:
            name = "fake"
            description = "说明" * 50
            args = {}

        first = measure_tool_tokens([FakeTool()])
        second = measure_tool_tokens([FakeTool()])

        self.assertGreater(first, 0)
        self.assertEqual(first, second)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder
```

Expected: FAIL with `ImportError: cannot import name 'build_view'`.

- [ ] **Step 3: Implement the view builder**

Append to `context_builder.py`:

```python
@dataclass(frozen=True)
class ContextStats:
    raw_tokens: int
    view_tokens: int
    window: int
    tool_tokens: int
    system_tokens: int
    summary_tokens: int
    recent_turns: int
    dropped_turns: int
    images_dropped: int
    fits: bool

    def as_dict(self) -> dict:
        return {
            "raw_tokens": self.raw_tokens,
            "view_tokens": self.view_tokens,
            "window": self.window,
            "tool_tokens": self.tool_tokens,
            "system_tokens": self.system_tokens,
            "summary_tokens": self.summary_tokens,
            "recent_turns": self.recent_turns,
            "dropped_turns": self.dropped_turns,
            "images_dropped": self.images_dropped,
            "fits": self.fits,
        }


def split_turns(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    turns: list[list[BaseMessage]] = []
    for message in messages:
        if isinstance(message, HumanMessage) and turns:
            turns.append([message])
        elif not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def _copy_message(message: BaseMessage, content) -> BaseMessage:
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    return message.copy(update={"content": content})


def _replace_images(message: BaseMessage) -> tuple[BaseMessage, int]:
    content = getattr(message, "content", "")
    if not isinstance(content, list):
        return message, 0
    parts: list = []
    dropped = 0
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}:
            parts.append({"type": "text", "text": "[图片已省略]"})
            dropped += 1
        else:
            parts.append(part)
    if not dropped:
        return message, 0
    return _copy_message(message, parts), dropped


def _truncate_message(message: BaseMessage, max_tokens: int) -> BaseMessage:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        remaining = max_tokens
        parts: list = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text", ""))
                cut = truncate_to_tokens(text, remaining)
                if cut != text:
                    changed = True
                parts.append({**part, "text": cut})
                remaining = max(0, remaining - estimate_text_tokens(cut))
            else:
                parts.append(part)
        return _copy_message(message, parts) if changed else message
    text = str(content)
    cut = truncate_to_tokens(text, max_tokens)
    return _copy_message(message, cut) if cut != text else message


def measure_tool_tokens(tools: Iterable) -> int:
    tools = list(tools)
    key = tuple(sorted(getattr(tool, "name", "") for tool in tools))
    cached = _tool_token_cache.get(key)
    if cached is not None:
        return cached
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        payload = [convert_to_openai_tool(tool) for tool in tools]
        value = estimate_text_tokens(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        value = 2000
    _tool_token_cache[key] = value
    return value


def build_view(
    session,
    profile,
    system_prompt: str,
    tool_tokens: int,
    hydrate: Callable[[list[BaseMessage]], list[BaseMessage]] | None = None,
) -> tuple[list[BaseMessage], ContextStats]:
    messages = list(getattr(session, "messages", []) or [])
    system_tokens = estimate_text_tokens(system_prompt) + MESSAGE_OVERHEAD
    budget = compute_budget(profile.context_window, system_tokens, tool_tokens)
    raw_tokens = estimate_tokens(messages)

    summary_text = truncate_to_tokens(
        getattr(session, "summary", "") or "",
        budget["summary_cap"],
        SUMMARY_TRUNCATE_MARKER,
    )
    summary_message = (
        SystemMessage(content=f"{SUMMARY_PREFIX}\n{summary_text}")
        if summary_text
        else None
    )
    summary_tokens = estimate_tokens([summary_message]) if summary_message else 0

    remaining = budget["conversation"] - summary_tokens
    turns = split_turns(messages)
    selected: list[list[BaseMessage]] = []
    images_dropped = 0
    for turn in reversed(turns):
        prepared = []
        for message in turn:
            if not profile.supports_vision:
                message, dropped = _replace_images(message)
                images_dropped += dropped
            prepared.append(message)
        cost = estimate_tokens(prepared)
        if selected and cost > remaining:
            break
        selected.insert(0, prepared)
        remaining -= cost
    if not selected and turns:
        prepared = []
        for message in turns[-1]:
            if not profile.supports_vision:
                message, dropped = _replace_images(message)
                images_dropped += dropped
            prepared.append(_truncate_message(message, budget["max_message"]))
        selected = [prepared]

    view: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    if summary_message is not None:
        view.append(summary_message)
    for turn in selected:
        view.extend(turn)
    view = sanitize_messages(view)
    if profile.supports_vision and hydrate is not None:
        view = hydrate(view)
    view_tokens = estimate_tokens(view)
    stats = ContextStats(
        raw_tokens=raw_tokens,
        view_tokens=view_tokens,
        window=profile.context_window,
        tool_tokens=tool_tokens,
        system_tokens=system_tokens,
        summary_tokens=summary_tokens,
        recent_turns=len(selected),
        dropped_turns=max(0, len(turns) - len(selected)),
        images_dropped=images_dropped,
        fits=view_tokens <= profile.context_window,
    )
    return view, stats
```

- [ ] **Step 4: Run the tests and fix the expected-value details**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder
```

Expected: PASS. If `test_summary_is_injected_and_capped` fails because the summary plus system prompt exceeds the 4096 window, use `profile(window=8192)` in that test; the behavior under test is summary injection, not the tiny window.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add context_builder.py tests/test_context_builder.py
git commit -m "feat: build per-model context views without deleting history"
```

---

### Task 5: Rolling summary maintenance

**Files:**
- Modify: `Langgraph/context_builder.py`
- Test: `Langgraph/tests/test_context_builder.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_builder.py`:

```python
from context_builder import SummaryError, ensure_summary


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise RuntimeError("没有更多回复")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


def big_session(count=30, size=400):
    messages = []
    for index in range(count):
        factory = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(factory(content="内容" * size))
    session_obj = SimpleNamespace(
        messages=messages,
        summary="",
        summary_upto=0,
        summary_count=0,
        summary_updated_at="",
    )
    return session_obj


def local_profile(window=16384):
    return SimpleNamespace(context_window=window, supports_vision=False, key="local", provider="ollama")


def cloud_profile():
    return SimpleNamespace(context_window=128000, supports_vision=False, key="deepseek", provider="deepseek")


class SummaryMaintenanceTests(unittest.TestCase):
    def test_short_history_is_not_summarized(self):
        session_obj = big_session(count=4)
        model = FakeModel(["摘要"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertFalse(changed)
        self.assertEqual(model.calls, 0)

    def test_summary_advances_to_a_turn_boundary_and_keeps_two_turns(self):
        session_obj = big_session()
        model = FakeModel(["摘要"] * 5)

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertTrue(changed)
        self.assertGreater(session_obj.summary_upto, 0)
        self.assertEqual(session_obj.summary_upto % 2, 0)
        self.assertGreaterEqual(len(session_obj.messages) - session_obj.summary_upto, 4)

    def test_large_gap_is_chunked_and_merged(self):
        session_obj = big_session(count=40, size=900)
        model = FakeModel(["第一段", "第二段", "第三段"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertTrue(changed)
        self.assertGreaterEqual(model.calls, 2)
        self.assertIn("第一段", session_obj.summary)

    def test_summary_is_capped(self):
        session_obj = big_session()
        model = FakeModel(["长" * 100000] * 5)

        ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertLessEqual(
            estimate_text_tokens(session_obj.summary),
            2000 + estimate_text_tokens("…[摘要已截断]"),
        )

    def test_failure_raises_and_keeps_previous_summary(self):
        session_obj = big_session()
        session_obj.summary = "旧摘要"

        with self.assertRaises(SummaryError):
            ensure_summary(
                session_obj, cloud_profile(), local_profile(), "系统", 0,
                lambda key: FakeModel([RuntimeError("模型不可用")]),
                lambda session: None,
            )

        self.assertEqual(session_obj.summary, "旧摘要")
        self.assertEqual(session_obj.summary_upto, 0)

    def test_force_ignores_the_threshold(self):
        session_obj = big_session(count=10)
        model = FakeModel(["手动摘要"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None, force=True,
        )

        self.assertTrue(changed)
        self.assertEqual(model.calls, 1)
        self.assertGreater(session_obj.summary_upto, 0)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder.SummaryMaintenanceTests
```

Expected: FAIL with `ImportError: cannot import name 'ensure_summary'`.

- [ ] **Step 3: Implement `ensure_summary`**

Append to `context_builder.py`:

```python
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _turn_starts(messages: Sequence[BaseMessage]) -> list[int]:
    starts = [0]
    for index, message in enumerate(messages):
        if index > 0 and isinstance(message, HumanMessage):
            starts.append(index)
    return starts


def _summary_prompt(previous: str, chunk: Sequence[BaseMessage], summary_cap: int) -> str:
    lines: list[str] = []
    for message in chunk:
        role = {"human": "用户", "ai": "助手", "tool": "工具"}.get(
            getattr(message, "type", ""), "消息"
        )
        text = _content_text(getattr(message, "content", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    return (
        "请把下面这段新增对话合并进已有摘要，输出一份简洁的中文摘要。\n"
        "保留关键事实、结论、涉及的文件路径和未完成事项；不要编造内容。\n"
        f"摘要控制在约 {max(200, summary_cap)} 个汉字以内。\n\n"
        f"已有摘要：\n{previous or '（无）'}\n\n"
        f"新增对话：\n{transcript}"
    )


def ensure_summary(
    session,
    active_profile,
    target_profile,
    system_prompt: str,
    tool_tokens: int,
    build_model: Callable,
    save_session: Callable,
    force: bool = False,
) -> bool:
    """Summarize older turns into ``session.summary`` without deleting messages.

    ``target_profile`` is the small model the summary must be ready for; the
    summary is generated with ``active_profile`` (the cloud model).
    """
    messages = list(getattr(session, "messages", []) or [])
    system_tokens = estimate_text_tokens(system_prompt) + MESSAGE_OVERHEAD
    budget = compute_budget(target_profile.context_window, system_tokens, tool_tokens)
    tail_target = budget["tail_target"]
    summary_cap = budget["summary_cap"]

    start = int(getattr(session, "summary_upto", 0) or 0)
    if start < 0 or start > len(messages):
        start = 0
    if len(messages) - start <= MIN_KEEP_TURNS:
        return False

    starts = _turn_starts(messages)
    turn_count = len(starts) if messages else 0
    keep = min(MIN_KEEP_TURNS, turn_count)
    cutoff: int | None = None
    if force:
        cutoff = starts[turn_count - keep] if turn_count > keep else starts[0]
    else:
        if estimate_tokens(messages[start:]) <= tail_target:
            return False
        for boundary in starts:
            if boundary < start:
                continue
            if estimate_tokens(messages[boundary:]) <= tail_target:
                cutoff = boundary
                break
        if cutoff is None:
            cutoff = starts[turn_count - keep] if turn_count > keep else starts[0]
    cutoff = max(start, min(cutoff, len(messages)))
    gap = messages[start:cutoff]
    if not gap:
        return False

    chunks: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    current_tokens = 0
    for message in gap:
        cost = estimate_tokens([message])
        if current and current_tokens + cost > MAX_SUMMARY_CHUNK_TOKENS:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += cost
    if current:
        chunks.append(current)

    summary = getattr(session, "summary", "") or ""
    covered = start
    calls = 0
    for chunk in chunks:
        if calls >= MAX_SUMMARY_CALLS:
            break
        try:
            response = build_model(active_profile.key).invoke(
                [HumanMessage(content=_summary_prompt(summary, chunk, summary_cap))]
            )
        except Exception as exc:  # noqa: BLE001
            if covered > start:
                session.summary = summary
                session.summary_upto = covered
                session.summary_count = covered
                session.summary_updated_at = _now_iso()
                save_session(session)
            raise SummaryError(str(exc)) from exc
        text = _content_text(getattr(response, "content", "")).strip()
        if not text:
            raise SummaryError("模型返回了空摘要")
        combined = f"{summary}\n{text}".strip() if summary else text
        summary = truncate_to_tokens(combined, summary_cap, SUMMARY_TRUNCATE_MARKER)
        covered += len(chunk)
        calls += 1
    if covered == start:
        return False

    session.summary = summary
    session.summary_upto = covered
    session.summary_count = covered
    session.summary_updated_at = _now_iso()
    save_session(session)
    return True
```

- [ ] **Step 4: Run the tests and adjust if needed**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_builder
```

Expected: PASS. If `test_summary_advances_to_a_turn_boundary_and_keeps_two_turns` fails because the chosen cutoff is not even, verify that `_turn_starts` indexes are used; each turn starts on a HumanMessage, so even indexes are expected. If the gap is under 6000 tokens, add `size=600` to `big_session()` in that test.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add context_builder.py tests/test_context_builder.py
git commit -m "feat: maintain a rolling summary for the local fallback window"
```

---

### Task 6: Integrate views, summary, and non-destructive compaction into the API

**Files:**
- Modify: `Langgraph/desktop_api.py:1-36`, `:235-315`, `:352-465`, `:552-565`, `:683-699`, `:791-812`
- Modify: `Langgraph/model_registry.py:207-220`
- Test: `Langgraph/tests/test_context_compaction.py` (rewrite)

- [ ] **Step 1: Rewrite the API tests**

Replace `tests/test_context_compaction.py` with:

```python
"""Context view and rolling summary API tests."""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from desktop_api import create_app
from session_store import SessionStore


def make_messages(count: int, size: int = 400) -> list:
    messages = []
    for index in range(count):
        factory = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(factory(content="内容" * size))
    return messages


class FakeModel:
    def __init__(self, text: str = "摘要内容", error: Exception | None = None):
        self.text = text
        self.error = error

    def invoke(self, messages):
        if self.error:
            raise self.error
        return AIMessage(content=self.text)


class EchoGraph:
    def stream(self, payload, *args, **kwargs):
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="回答")]})


class ContextApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_context_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = patch.dict(os.environ, {"OLLAMA_CONTEXT_WINDOW": "16384"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = SessionStore(self.root / ".sessions")
        self.headers = {"X-Desktop-Token": "t"}

    def _app(self, model=None):
        return create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: EchoGraph(),
            model_factory=lambda key: model or FakeModel(),
        )

    def test_run_keeps_full_history_when_view_is_trimmed(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "继续"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        detail = client.get(f"/api/sessions/{session.id}", headers=self.headers).json()
        self.assertEqual(detail["message_count"], 32)
        self.assertGreater(detail["context"]["dropped_turns"], 0)
        self.assertTrue(detail["context"]["fits"])

    def test_compact_updates_summary_without_deleting_messages(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["message_count"], 30)
        self.assertGreater(body["summary_upto"], 0)
        self.assertTrue(body["summary"])
        self.assertEqual(body["summary_model"], "local")

    def test_compact_rejects_short_sessions(self):
        session = self.store.create("短会话", model_key="local")
        session.messages = make_messages(4)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 400)

    def test_compact_reports_summary_failure(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(
            self._app(FakeModel(error=RuntimeError("模型不可用")))
        )

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 502)

    def test_session_detail_exposes_context_and_summary_coverage(self):
        session = self.store.create("详情", model_key="local")
        session.messages = make_messages(4)
        self.store.save(session)
        client = TestClient(self._app())

        body = client.get(f"/api/sessions/{session.id}", headers=self.headers).json()

        self.assertEqual(body["summary_upto"], 0)
        self.assertIn("context", body)
        self.assertEqual(body["context"]["window"], 16384)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_compaction
```

Expected: FAIL because `create_app` does not accept `model_factory`, or because `context` is missing.

- [ ] **Step 3: Update imports and remove the old compaction code**

At the top of `desktop_api.py`, add:

```python
import socket
from urllib.parse import urlparse
```

Replace the `graph_agent` import line with:

```python
from graph_agent import SYSTEM_PROMPT, build_graph, build_llm, collect_tools
```

Add the context builder imports:

```python
from context_builder import (
    ContextBudgetError,
    SummaryError,
    build_view,
    ensure_summary,
    measure_tool_tokens,
)
```

Delete `COMPACT_RATIO`, `COMPACT_KEEP_RECENT`, `SUMMARY_PREFIX`, `IMAGE_TOKEN_ESTIMATE`, `_image_part_count`, `estimate_tokens`, `summarize_messages`, and `compact_session` from `desktop_api.py` (lines 235-315 in the current file).

- [ ] **Step 4: Add helpers in `create_app`**

Immediately after `def create_app(...)`, replace the `summarizer=None` parameter with:

```python
    model_factory=None,
```

Inside `create_app`, before `run_graph`, add:

```python
    model_factory = model_factory or build_llm

    def _extra_tools():
        try:
            return build_mcp_tools(mcp, mcp.enabled_ids())
        except Exception:  # noqa: BLE001
            return []

    def _tool_tokens_for(key: str, extra_tools=None) -> int:
        try:
            return measure_tool_tokens(
                collect_tools(manager, model_key=key, extra_tools=extra_tools or [])
            )
        except Exception:  # noqa: BLE001
            return 2000

    def _fallback_profile(exclude_key: str):
        candidates = [
            profile
            for profile in list_profiles()
            if profile.provider == "ollama" and profile.key != exclude_key
        ]
        candidates.sort(key=lambda profile: (profile.key != "local", profile.key))
        return candidates[0] if candidates else None

    def _context_json(session, key: str):
        try:
            profile = get_profile(key)
            _, stats = build_view(
                session,
                profile,
                SYSTEM_PROMPT,
                _tool_tokens_for(key, _extra_tools()),
            )
            return stats.as_dict()
        except Exception:  # noqa: BLE001
            return None
```

- [ ] **Step 5: Replace `run_graph`**

Replace the entire `run_graph` function (lines 352-465) with:

```python
    def run_graph(run: RunState, session, text: str) -> None:
        from langchain_core.messages import AIMessageChunk

        tool_started: dict[str, float] = {}
        emitted_tool_end: set[str] = set()
        final_key = effective_model_key(session)

        def emit_tool_end(message: ToolMessage) -> None:
            call_id = message.tool_call_id
            if call_id in emitted_tool_end:
                return
            emitted_tool_end.add(call_id)
            emit(
                run,
                "tool_end",
                {
                    "run_id": run.run_id,
                    "tool": message.name or "tool",
                    "call_id": call_id,
                    "status": "error" if str(message.content).startswith("[错误]") else "ok",
                    "duration_ms": int(
                        (time.monotonic() - tool_started.get(call_id, time.monotonic())) * 1000
                    ),
                },
            )

        def attempt(key: str, profile, allow_summary: bool):
            tool_started.clear()
            emitted_tool_end.clear()
            extra_tools = [] if profile.provider == "ollama" else _extra_tools()
            tool_tokens = _tool_tokens_for(key, extra_tools)
            if allow_summary and profile.provider != "ollama":
                fallback = _fallback_profile(key)
                if fallback is not None:
                    try:
                        ensure_summary(
                            session,
                            profile,
                            fallback,
                            SYSTEM_PROMPT,
                            _tool_tokens_for(fallback.key),
                            model_factory,
                            store.save,
                        )
                    except Exception:  # noqa: BLE001
                        # 摘要失败不阻塞本次对话
                        pass
            view, stats = build_view(
                session,
                profile,
                SYSTEM_PROMPT,
                tool_tokens,
                hydrate=lambda items: hydrate_messages(items, attachment_store),
            )
            if not stats.fits:
                raise ContextBudgetError(
                    f"{profile.label} 的上下文窗口放不下系统提示和工具："
                    f"视图 {stats.view_tokens} tokens，窗口 {profile.context_window}。"
                )
            graph = graph_builder(manager, model_key=key, extra_tools=extra_tools)
            initial_count = len(view)
            final_messages = view
            for mode, payload in graph.stream(
                {"messages": view},
                config={"recursion_limit": 25},
                stream_mode=["messages", "updates", "values"],
            ):
                if run.cancel.is_set():
                    break
                if mode == "messages":
                    message, _meta = payload
                    if isinstance(message, AIMessageChunk):
                        text_chunk = _text(message.content)
                        if text_chunk:
                            emit(run, "token", {"run_id": run.run_id, "text": text_chunk})
                    elif isinstance(message, ToolMessage):
                        emit_tool_end(message)
                elif mode == "updates":
                    for _node, update in payload.items():
                        for message in update.get("messages", []):
                            if isinstance(message, AIMessage) and message.tool_calls:
                                for call in message.tool_calls:
                                    call_id = call.get("id") or "unknown"
                                    tool_started[call_id] = time.monotonic()
                                    emit(
                                        run,
                                        "tool_start",
                                        {
                                            "run_id": run.run_id,
                                            "tool": call.get("name", "tool"),
                                            "call_id": call_id,
                                            "args": call.get("args", {}),
                                        },
                                    )
                            elif isinstance(message, ToolMessage):
                                emit_tool_end(message)
                elif mode == "values":
                    final_messages = payload["messages"]
            return final_messages[initial_count:]

        try:
            key = effective_model_key(session)
            profile = get_profile(key)
            session.model_key = key
            manager.set_model_key(key)
            manager.set_current_session(session.id)
            broker = ApprovalBroker(
                run.run_id,
                session.id,
                lambda event, data: emit(run, event, data),
                undo_entries.setdefault(session.id, []),
            )
            run.broker = broker
            set_approval_broker(broker)
            try:
                new_messages = attempt(key, profile, allow_summary=True)
                final_key = key
            except Exception as exc:
                fallback = _fallback_profile(key)
                can_fallback = (
                    profile.provider != "ollama"
                    and fallback is not None
                    and is_network_failure(exc)
                    and _probe_local(fallback.base_url)[0]
                )
                if not can_fallback:
                    raise
                reason = str(exc).strip()[:200] or "网络不可用"
                emit(
                    run,
                    "model_fallback",
                    {
                        "run_id": run.run_id,
                        "from": key,
                        "to": fallback.key,
                        "label": fallback.label,
                        "reason": reason,
                        "restart": True,
                    },
                )
                session.model_key = fallback.key
                store.save(session)
                manager.set_model_key(fallback.key)
                try:
                    new_messages = attempt(fallback.key, fallback, allow_summary=False)
                    final_key = fallback.key
                except Exception as local_exc:
                    raise RuntimeError(
                        f"云端连接失败（{exc}）；本地模型重试也失败（{local_exc}）"
                    ) from local_exc
            session.messages.extend(dehydrate_messages(new_messages, attachment_store))
            store.save(session)
        except Exception as exc:
            emit(run, "error", {"run_id": run.run_id, "code": "run_error", "message": str(exc)})
        finally:
            set_approval_broker(None)
            with runs_lock:
                if active_runs.get(session.id) == run.run_id:
                    active_runs.pop(session.id, None)
            emit(
                run,
                "done",
                {
                    "run_id": run.run_id,
                    "interrupted": run.cancel.is_set(),
                    "model_key": final_key,
                },
            )
            run.events.put(None)
```

- [ ] **Step 6: Add the network classifier at module level**

Above `def create_app`, add:

```python
_NETWORK_EXC_NAMES = {
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "APIConnectionError",
    "APITimeoutError",
    "gaierror",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
}
_NETWORK_HINTS = (
    "connection refused",
    "timed out",
    "getaddrinfo failed",
    "network is unreachable",
    "temporary failure in name resolution",
    "connection reset",
)


def is_network_failure(exc: BaseException) -> bool:
    """Return True for connection/timeout failures, not HTTP status errors."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = {base.__name__ for base in type(current).__mro__}
        if names & _NETWORK_EXC_NAMES:
            return True
        text = str(current).lower()
        if any(hint in text for hint in _NETWORK_HINTS):
            return True
        current = current.__cause__ or current.__context__
    return False
```

- [ ] **Step 7: Update `session_json`**

Replace the body of `session_json` with:

```python
    def session_json(session, attachment_url=None) -> dict:
        key = effective_model_key(session)
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model_key": key,
            "message_count": len(session.messages),
            "summary": session.summary,
            "summary_count": session.summary_count,
            "summary_upto": session.summary_upto,
            "context": _context_json(session, key),
            "messages": [
                _message_json(m, attachment_url) for m in session.messages
            ],
        }
```

- [ ] **Step 8: Update the model switch and compact endpoints**

In `switch_model`, replace the return with:

```python
        session.model_key = key
        store.save(session)
        manager.set_model_key(key)
        return {
            "id": session_id,
            "model_key": key,
            "label": profile.label,
            "context": _context_json(session, key),
        }
```

Replace the `compact_now` endpoint body with:

```python
    @app.post("/api/sessions/{session_id}/compact", dependencies=[Depends(require_token)])
    def compact_now(request: Request, session_id: str) -> dict:
        with runs_lock:
            if session_id in active_runs:
                raise HTTPException(status_code=409, detail="已有回复正在生成")
            try:
                session = store.load(session_id)
            except SessionStoreError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            key = effective_model_key(session)
            try:
                profile = get_profile(key)
            except ModelConfigError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target = _fallback_profile(key) or profile
            try:
                changed = ensure_summary(
                    session,
                    profile,
                    target,
                    SYSTEM_PROMPT,
                    _tool_tokens_for(target.key),
                    model_factory,
                    store.save,
                    force=True,
                )
            except SummaryError as exc:
                raise HTTPException(status_code=502, detail=f"压缩失败：{exc}") from exc
            if not changed:
                raise HTTPException(status_code=400, detail="没有可更新的摘要")
            payload = session_json(session, attachment_link(request))
            payload["summary_model"] = key
            return payload
```

- [ ] **Step 9: Add the connect timeout to cloud clients**

In `model_registry.py`, `build_chat_model`, add `http_client`:

```python
    from httpx import Client, Timeout

    return ChatOpenAI(
        model=profile.model,
        api_key=profile.api_key or "ollama",
        base_url=profile.base_url,
        temperature=0,
        timeout=profile.timeout,
        max_retries=profile.max_retries,
        http_client=Client(timeout=Timeout(profile.timeout, connect=5.0)),
    )
```

- [ ] **Step 10: Run the tests**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_context_compaction tests.test_desktop_api
```

Expected: `test_context_compaction` PASS. `test_desktop_api` may fail because `FakeGraph` returns only two messages; update `FakeGraph.stream` in `tests/test_desktop_api.py` to echo the payload:

```python
    def stream(self, payload, *args, **kwargs):
        yield ("messages", (FakeChunk(content="你"), {}))
        yield ("messages", (FakeChunk(content="好"), {}))
        yield ("updates", {"agent": {"messages": [AIMessage(content="你好")]}})
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="你好")]})
```

Then rerun until all tests pass.

- [ ] **Step 11: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop_api.py model_registry.py tests/test_context_compaction.py tests/test_desktop_api.py
git commit -m "feat: build model views and update summaries without deleting history"
```

---

### Task 7: Health endpoint and API fallback tests

**Files:**
- Modify: `Langgraph/desktop_api.py` (inside `create_app`, near `/api/models`)
- Create: `Langgraph/tests/test_model_fallback.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model_fallback.py`:

```python
"""Automatic local fallback and health endpoint tests."""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from desktop_api import create_app
from session_store import SessionStore


class NetworkErrorGraph:
    def stream(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")
        yield  # pragma: no cover


class StaticErrorGraph:
    def __init__(self, exc: Exception):
        self.exc = exc

    def stream(self, *args, **kwargs):
        raise self.exc
        yield  # pragma: no cover


class LocalGraph:
    def stream(self, payload, *args, **kwargs):
        yield ("messages", (AIMessageChunk(content="本地"), {}))
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="本地回答")]})


class FallbackApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_fallback_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "OLLAMA_CONTEXT_WINDOW": "16384"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = SessionStore(self.root / ".sessions")
        self.headers = {"X-Desktop-Token": "t"}

    def _session(self):
        return self.store.create("降级", model_key="deepseek").id

    def _app(self, builder):
        return create_app(root=self.root, token="t", graph_builder=builder)

    def test_network_error_falls_back_and_retries_once(self):
        sid = self._session()
        calls: list[str] = []

        def builder(manager, model_key, extra_tools=None):
            calls.append(model_key)
            return NetworkErrorGraph() if model_key == "deepseek" else LocalGraph()

        client = TestClient(self._app(builder))
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: model_fallback", response.text)
        self.assertIn('"model_key": "local"', response.text)
        self.assertEqual(calls, ["deepseek", "local"])

        detail = client.get(f"/api/sessions/{sid}", headers=self.headers).json()
        self.assertEqual(detail["model_key"], "local")
        self.assertEqual([m["role"] for m in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][-1]["content"], "本地回答")

    def test_auth_error_does_not_fall_back(self):
        sid = self._session()
        client = TestClient(
            self._app(
                lambda manager, model_key, extra_tools=None: StaticErrorGraph(
                    RuntimeError("401 Unauthorized")
                )
            )
        )
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertNotIn("event: model_fallback", response.text)
        self.assertIn("event: error", response.text)
        detail = client.get(f"/api/sessions/{sid}", headers=self.headers).json()
        self.assertEqual(detail["model_key"], "deepseek")

    def test_local_unavailable_does_not_fall_back(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: NetworkErrorGraph())
        )
        with patch("desktop_api._probe_local", return_value=(False, "Ollama 未启动")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertNotIn("event: model_fallback", response.text)
        self.assertIn("event: error", response.text)

    def test_retry_failure_reports_both_causes(self):
        sid = self._session()

        def builder(manager, model_key, extra_tools=None):
            if model_key == "deepseek":
                return NetworkErrorGraph()
            return StaticErrorGraph(RuntimeError("local boom"))

        client = TestClient(self._app(builder))
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertIn("event: model_fallback", response.text)
        self.assertIn("本地模型重试也失败", response.text)

    def test_local_health_uses_local_probe(self):
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )
        with patch("desktop_api._probe_local", return_value=(True, "")):
            body = client.get("/api/models/local/health", headers=self.headers).json()

        self.assertTrue(body["reachable"])
        self.assertEqual(body["key"], "local")

    def test_cloud_health_uses_tcp_probe(self):
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )
        with patch("desktop_api._probe_cloud", return_value=(False, "无法连接")):
            body = client.get("/api/models/deepseek/health", headers=self.headers).json()

        self.assertFalse(body["reachable"])

    def test_session_detail_exposes_context(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )

        body = client.get(f"/api/sessions/{sid}", headers=self.headers).json()

        self.assertIn("context", body)
        self.assertEqual(body["context"]["window"], 128000)

    def test_model_switch_returns_context_preview(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )

        body = client.patch(
            f"/api/sessions/{sid}/model",
            json={"model_key": "local"},
            headers=self.headers,
        ).json()

        self.assertEqual(body["model_key"], "local")
        self.assertEqual(body["context"]["window"], 16384)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_model_fallback
```

Expected: FAIL with `AttributeError: module 'desktop_api' has no attribute '_probe_cloud'` or a 404 for `/api/models/{key}/health`.

- [ ] **Step 3: Add `_probe_cloud` and the health endpoint**

At module level in `desktop_api.py`, next to `_probe_local`, add:

```python
def _probe_cloud(base_url: str) -> tuple[bool, str]:
    """Check TCP reachability of a cloud endpoint without sending credentials."""
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        return False, "无效的模型地址"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True, ""
    except OSError as exc:  # noqa: BLE001
        return False, f"无法连接 {host}:{port}（{exc}）"
```

Inside `create_app`, after the existing `/api/models` endpoint, add:

```python
    @app.get("/api/models/{key}/health", dependencies=[Depends(require_token)])
    def model_health(key: str) -> dict:
        try:
            profile = get_profile(key)
        except ModelConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if profile.provider == "ollama":
            reachable, reason = _probe_local(profile.base_url)
        elif not profile.api_key:
            reachable, reason = False, "未配置 API Key"
        else:
            reachable, reason = _probe_cloud(profile.base_url)
        return {"key": profile.key, "reachable": reachable, "reason": reason}
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_model_fallback
```

Expected: PASS. If `test_session_detail_exposes_context` fails with `context: None`, inspect `_context_json`: the local tool schema must be measured with `collect_tools(model_key=key)`, and `OLLAMA_CONTEXT_WINDOW` must be 16384.

- [ ] **Step 5: Run the full backend suite**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest discover -s tests -v
```

Expected: PASS. Fix any fake graph that does not echo `payload["messages"]`; the new persistence logic appends only messages after the initial view.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop_api.py tests/test_model_fallback.py
git commit -m "feat: auto-fallback to local on network failures with health probe"
```

---

### Task 8: Frontend context types and display

**Files:**
- Modify: `Langgraph/desktop/src/types.ts`
- Modify: `Langgraph/desktop/src/api.ts`
- Modify: `Langgraph/desktop/src/components/Chat.tsx`
- Modify: `Langgraph/desktop/src/components/RightPanel.tsx`
- Delete: `Langgraph/desktop/src/helpers.test.ts` (estimateContext tests)
- Modify: `Langgraph/desktop/src/helpers.ts`
- Test: `Langgraph/desktop/src/components/Chat.test.tsx`

- [ ] **Step 1: Update types and API**

In `desktop/src/types.ts`, add:

```ts
export interface ContextStats {
  raw_tokens: number;
  view_tokens: number;
  window: number;
  tool_tokens: number;
  system_tokens: number;
  summary_tokens: number;
  recent_turns: number;
  dropped_turns: number;
  images_dropped: number;
  fits: boolean;
}

export interface ModelHealth {
  key: string;
  reachable: boolean;
  reason?: string;
}
```

In `SessionDetail`, add:

```ts
  summary_upto?: number;
  context?: ContextStats;
```

In `desktop/src/api.ts`, change `patchSessionModel` to:

```ts
export async function patchSessionModel(
  id: string,
  modelKey: string,
): Promise<{ model_key: string; label: string; context?: ContextStats }> {
  return request(`/api/sessions/${id}/model`, {
    method: "PATCH",
    body: JSON.stringify({ model_key: modelKey }),
  });
}
```

Add `ContextStats` to the type import at the top, and add:

```ts
export async function getModelHealth(key: string): Promise<ModelHealth> {
  return request<ModelHealth>(`/api/models/${encodeURIComponent(key)}/health`);
}
```

Import `ModelHealth` in the type import list.

- [ ] **Step 2: Remove the client estimator**

In `desktop/src/helpers.ts`, delete `estimateContext`. Delete `desktop/src/helpers.test.ts`.

In `desktop/src/components/Chat.tsx`, change the import:

```tsx
import { copyText, formatCount, lastAssistantIndex } from "../helpers";
```

Replace the subtitle context block with:

```tsx
            {props.session.context && props.session.context.window
              ? ` · 上下文 ≈ ${formatCount(props.session.context.view_tokens)} / ${formatCount(
                  props.session.context.window,
                )} tokens`
              : ""}
```

Replace the summary banner with:

```tsx
        {session.summary && (
          <details className="compacted-summary">
            <summary>
              已摘要 {session.summary_upto ?? session.summary_count ?? 0} 条早期消息（原文仍保留）
            </summary>
            <pre className="compacted-summary-text">{session.summary}</pre>
          </details>
        )}
```

- [ ] **Step 3: Update the context panel**

In `desktop/src/components/RightPanel.tsx`, remove `estimateContext` from the helpers import. Replace:

```tsx
  const windowSize = props.model?.context_window ?? 0;
  const used = estimateContext(props.messages);
  const percent = windowSize > 0 ? Math.min(100, (used / windowSize) * 100) : 0;
  const messageTokens = (role: Message["role"]) =>
    estimateContext(props.messages.filter((message) => message.role === role));
```

with:

```tsx
  const context = props.session?.context;
  const windowSize = context?.window ?? props.model?.context_window ?? 0;
  const used = context?.view_tokens ?? 0;
  const percent = windowSize > 0 ? Math.min(100, (used / windowSize) * 100) : 0;
```

Delete the entire `context-breakdown` block (the per-role bars). Replace the context stats block with:

```tsx
          <div className="context-stats">
            <div>
              <b>{formatCount(context?.raw_tokens ?? 0)}</b>
              <span>原始历史</span>
            </div>
            <div>
              <b>{props.session?.summary_upto ?? 0}</b>
              <span>已摘要条数</span>
            </div>
            <div>
              <b>{context?.dropped_turns ?? 0}</b>
              <span>裁剪轮次</span>
            </div>
            <div>
              <b>{context?.images_dropped ?? 0}</b>
              <span>省略图片</span>
            </div>
          </div>
```

Change the button label:

```tsx
            {props.compacting ? "正在更新…" : "更新摘要"}
```

Replace the note with:

```tsx
          <div className="context-note">
            上下文由后端按当前模型窗口统计；较早消息仍保留在历史中，只影响发给模型的内容。
          </div>
```

- [ ] **Step 4: Update the Chat tests**

In `desktop/src/components/Chat.test.tsx`, import the session type:

```tsx
import type { SessionDetail } from "../types";
```

Annotate the session object in `makeProps()` so the new optional fields are
visible to TypeScript:

```tsx
    const session: SessionDetail = {
      id: "s1",
      title: "会话",
      created_at: "2026-01-01T00:00:00+08:00",
      updated_at: "2026-01-01T00:00:00+08:00",
      message_count: 2,
      model_key: "local",
      messages: [],
      summary: "",
      summary_count: 0,
      summary_upto: 0,
    };
```

Then return `{ session, messages: [...], ... }` from `makeProps()` instead of
the inline session object. Change the summary banner assertion to:

```tsx
    expect(screen.getByText("已摘要 12 条早期消息（原文仍保留）")).toBeTruthy();
```

Add a context subtitle assertion:

```tsx
  it("shows server-provided context usage", () => {
    const props = makeProps();
    props.session.context = {
      raw_tokens: 31000,
      view_tokens: 9800,
      window: 16384,
      tool_tokens: 3200,
      system_tokens: 620,
      summary_tokens: 900,
      recent_turns: 2,
      dropped_turns: 9,
      images_dropped: 0,
      fits: true,
    };

    render(<Chat {...props} />);

    expect(screen.getByText(/9.8k \/ 16.4k tokens/)).toBeTruthy();
  });
```

- [ ] **Step 5: Run the frontend tests**

```bash
cd D:/Vibecoding/Langgraph/desktop
npx vitest run src/components/Chat.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop/src/types.ts desktop/src/api.ts desktop/src/helpers.ts desktop/src/components/Chat.tsx desktop/src/components/RightPanel.tsx desktop/src/components/Chat.test.tsx
git rm desktop/src/helpers.test.ts
git commit -m "feat: show server-provided context usage in the desktop UI"
```

---

### Task 9: Frontend fallback banner and recovery polling

**Files:**
- Modify: `Langgraph/desktop/src/App.tsx`
- Modify: `Langgraph/desktop/src/components/Chat.tsx`
- Modify: `Langgraph/desktop/src/styles.css`
- Test: `Langgraph/desktop/src/App.test.tsx`, `Langgraph/desktop/src/components/Chat.test.tsx`

- [ ] **Step 1: Add the UI and tests**

In `desktop/src/components/Chat.tsx`, add optional props to the `Props` type:

```tsx
  fallback?: { from: string; to: string; label: string; reason?: string } | null;
  networkBack?: boolean;
  switchingBack?: boolean;
  onSwitchBack?: () => void;
```

Render the banner immediately after the closing `</header>`:

```tsx
      {props.fallback && (
        <div className="fallback-banner" role="status">
          <span>
            网络不可用，已自动切换到本地模型
            {props.fallback.label ? `（${props.fallback.label}）` : ""}。
            本地模型只启用核心工具（无 Office / 子 agent / MCP）。
          </span>
          {props.networkBack && (
            <button
              type="button"
              onClick={props.onSwitchBack}
              disabled={props.switchingBack || props.streaming}
            >
              {props.switchingBack
                ? "切换中…"
                : `网络已恢复，切回 ${props.fallback.from}`}
            </button>
          )}
        </div>
      )}
```

In `desktop/src/styles.css`, add:

```css
.fallback-banner { display: flex; gap: 12px; align-items: center; justify-content: space-between; padding: 9px 16px; border-bottom: 1px solid var(--border); background: var(--panel-2); color: var(--text); font-size: 12px; }
.fallback-banner button { padding: 5px 10px; border: 1px solid var(--accent); border-radius: 6px; background: var(--panel); color: var(--text); cursor: pointer; white-space: nowrap; }
.fallback-banner button:disabled { opacity: 0.5; cursor: default; }
```

In `desktop/src/App.tsx`:

Add `getModelHealth` to the API import and add state:

```tsx
  const [fallback, setFallback] = useState<{
    from: string;
    to: string;
    label: string;
    reason?: string;
  } | null>(null);
  const [networkBack, setNetworkBack] = useState(false);
  const [switchingBack, setSwitchingBack] = useState(false);
```

Clear the banner when selecting or creating a session. In `selectSession` and `newSession`, after `setError("")`, add:

```tsx
    setFallback(null);
    setNetworkBack(false);
```

In `executeRun`, handle the event:

```tsx
        } else if (event.event === "model_fallback") {
          setFallback({
            from: String(event.data.from ?? ""),
            to: String(event.data.to ?? ""),
            label: String(event.data.label ?? ""),
            reason: String(event.data.reason ?? ""),
          });
          setNetworkBack(false);
          setStreamText("");
          setTools([]);
```

Add the polling effect before the return statement:

```tsx
  useEffect(() => {
    if (!fallback) return;
    let cancelled = false;
    const check = async () => {
      try {
        const health = await getModelHealth(fallback.from);
        if (!cancelled) setNetworkBack(health.reachable);
      } catch {
        // 网络探测失败时保持当前状态
      }
    };
    void check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [fallback?.from, fallback?.to]);
```

Add the switch-back function:

```tsx
  async function switchBack(): Promise<void> {
    if (!detail || !fallback || streaming) return;
    setSwitchingBack(true);
    try {
      await changeModel(fallback.from);
      setFallback(null);
      setNetworkBack(false);
    } finally {
      setSwitchingBack(false);
    }
  }
```

Pass the new props to `Chat`:

```tsx
        fallback={fallback}
        networkBack={networkBack}
        switchingBack={switchingBack}
        onSwitchBack={() => void switchBack()}
```

In `desktop/src/App.test.tsx`, add
`getModelHealth: vi.fn(async () => ({ key: "deepseek", reachable: true }))` to
the `vi.mock("./api")` object. Change the import line to:

```tsx
import {
  compactSession,
  getModelHealth,
  getSession,
  patchSessionModel,
  streamMessage,
} from "./api";
```

Then add:

```tsx
  it("shows the fallback banner and switches back", async () => {
    vi.mocked(streamMessage).mockImplementationOnce(async function* () {
      yield {
        event: "model_fallback",
        data: { from: "deepseek", to: "local", label: "本地 qwen3.5", reason: "连接失败" },
      };
      yield { event: "done", data: {} };
    });
    vi.mocked(patchSessionModel).mockResolvedValue({
      model_key: "deepseek",
      label: "DeepSeek",
    });
    render(<App />);

    fireEvent.click(await screen.findByText("重新生成"));

    expect(await screen.findByText(/已自动切换到本地模型/)).toBeTruthy();
    const button = await screen.findByText(/网络已恢复/);
    fireEvent.click(button);

    await waitFor(() =>
      expect(patchSessionModel).toHaveBeenCalledWith(
        "20260101-000000-abcd",
        "deepseek",
      ),
    );
  });
```

Also update the compact test button text from `"立即压缩上下文"` to `"更新摘要"`.

- [ ] **Step 2: Run the frontend tests**

```bash
cd D:/Vibecoding/Langgraph/desktop
npx vitest run src/App.test.tsx src/components/Chat.test.tsx
```

Expected: PASS. If the polling check runs before the stream event, `waitFor` handles it.

- [ ] **Step 3: Run the TypeScript build**

```bash
cd D:/Vibecoding/Langgraph/desktop
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop/src/App.tsx desktop/src/components/Chat.tsx desktop/src/styles.css desktop/src/App.test.tsx
git commit -m "feat: show local fallback banner and recovery switch button"
```

---

### Task 10: Development 16k Ollama model and manual verification

**Files:**
- Create (untracked): `Langgraph/Modelfile.qwen35-16k`
- Modify (untracked): `Langgraph/.env`

- [ ] **Step 1: Create the derived model**

```bash
cd D:/Vibecoding/Langgraph
cat > Modelfile.qwen35-16k <<'EOF'
FROM qwen3.5:2b
PARAMETER num_ctx 16384
EOF
ollama create qwen3.5:2b-16k -f Modelfile.qwen35-16k
```

Expected: `success`.

- [ ] **Step 2: Point the development environment at it**

Add or update these lines in `Langgraph/.env` (do not commit the file):

```text
OLLAMA_MODEL=qwen3.5:2b-16k
OLLAMA_CONTEXT_WINDOW=16384
```

- [ ] **Step 3: Verify the runtime context**

```bash
curl -s http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5:2b-16k","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
ollama ps
```

Expected: the `CONTEXT` column reads `16384`. If it still reads 4096, the derived model did not apply `num_ctx`; fall back to the native `/api/chat` path described in the spec and re-run the verification.

- [ ] **Step 4: Run the development app**

```bash
cd D:/Vibecoding/Langgraph/desktop
npm run dev
```

Do not run `npm run dist:win`. Do not rebuild or reinstall the package.

- [ ] **Step 5: Verify automatic fallback without unplugging the network**

Start the app with an unreachable cloud base URL for the session:

```bash
cd D:/Vibecoding/Langgraph/desktop
DEEPSEEK_BASE_URL=http://127.0.0.1:9 npm run dev
```

1. Create or select a session whose model is DeepSeek.
2. Send a message.
3. Expected: a `model_fallback` banner, the session switches to the local model, and the local model answers. The first local answer may take two to four minutes because of CPU prefill.
4. The session detail should still show the full message count; the context panel should show dropped turns and the 16k window.

- [ ] **Step 6: Verify recovery**

1. Restart the dev app without the `DEEPSEEK_BASE_URL` override.
2. In a session that was switched to local, wait up to 30 seconds for the health poll.
3. Expected: the banner shows `网络已恢复，切回 deepseek`.
4. Click it. The next reply uses DeepSeek and the full history is visible again.

- [ ] **Step 7: Run the full suites one last time**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest discover -s tests -v

cd D:/Vibecoding/Langgraph/desktop
npx vitest run
npx tsc --noEmit
```

Expected: all PASS.

- [ ] **Step 8: Commit only the tracked artifacts**

```bash
cd D:/Vibecoding/Langgraph
git status --short
```

Do not commit `Modelfile.qwen35-16k` or `.env` unless the user explicitly asks. No packaging changes are made.

---

## Self-Review Checklist

- Spec coverage:
  - Full transcript preserved: Tasks 1, 4, 6.
  - Per-model view and budget: Tasks 2, 4.
  - Rolling summary: Tasks 1, 5.
  - Automatic fallback and one retry: Tasks 6, 7.
  - Recovery button and health polling: Tasks 7, 9.
  - Core tool subset and 16k local runtime: Tasks 3, 10.
  - No destructive compaction: Task 6.
  - Frontend context statistics: Task 8.
- No placeholders: every code step contains concrete code and expected output.
- Type consistency: `ContextStats`, `summary_upto`, `model_factory`, `_fallback_profile`, `collect_tools`, and `getModelHealth` are defined once and reused with the same names.
