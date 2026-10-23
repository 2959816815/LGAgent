# 会话管理实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `graph_agent.py` 的终端 CLI 增加可跨重启保存的会话：新建、列出、切换、删除、清空，以及当前会话的分页历史查看。

**Architecture:** 新增 `session_store.py` 负责 JSON 持久化与 active 指针，新增 `session_cli.py` 负责命令解析/显示/确认；`graph_agent.py` 只增加启动恢复、命令分发和每轮自动保存。会话消息用 LangChain 的 `messages_to_dict()` / `messages_from_dict()` 序列化，复用现有 `build_graph()` 和全部工具。

**Tech Stack:** Python 3.12、LangGraph、LangChain Core message serialization、`json` / `pathlib` / `os.replace` / `secrets`、`unittest`。

**Spec:** `D:\Vibecoding\Langgraph\docs\superpowers\specs\2026-10-23-session-management-design.md`

---

## File Structure

| 文件 | 责任 |
|---|---|
| `D:\Vibecoding\Langgraph\session_store.py` | 数据模型、ID/路径校验、原子读写、会话列表/active/回退；不做终端输入输出 |
| `D:\Vibecoding\Langgraph\session_cli.py` | 斜杠命令解析、目标解析、列表/历史格式化、确认交互；不读写文件 |
| `D:\Vibecoding\Langgraph\graph_agent.py` | 启动恢复会话、命令分发、每轮保存、自动标题；不改 `build_graph()` 和工具 |
| `D:\Vibecoding\Langgraph\.gitignore` | 忽略 `.sessions/` |
| `D:\Vibecoding\Langgraph\test_session_store.py` | 存储层单元测试 |
| `D:\Vibecoding\Langgraph\test_session_cli.py` | 命令/显示层单元测试 |
| `D:\Vibecoding\Langgraph\test_session_flow.py` | 用 fake graph + 临时 store 测主循环集成 |

测试统一使用 `D:\Anaconda3\envs\rag\python.exe`，工作目录 `D:\Vibecoding\Langgraph`。Git 仓库根是 `D:\Vibecoding`；`.git` 在工作区外，执行 git 命令需要 `danger-full-access`。提交时只 stage 下面写明的文件，先检查 `git diff --cached --name-only`。

---

### Task 1: SessionStore 持久化

**Files:**
- Create: `D:\Vibecoding\Langgraph\session_store.py`
- Test: `D:\Vibecoding\Langgraph\test_session_store.py`

- [ ] **Step 1: 先写失败测试**

```python
# test_session_store.py
from __future__ import annotations

import json
import re
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from session_store import (
    DEFAULT_TITLE,
    SessionStore,
    SessionStoreError,
    clean_title,
)

SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_sessions_{uuid.uuid4().hex[:8]}"
        self.now = datetime(2026, 10, 23, 19, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.store = SessionStore(self.root, clock=self._clock)

    def _clock(self):
        current = self.now
        self.now += timedelta(seconds=1)
        return current

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_session_file_and_defaults(self):
        session = self.store.create()
        self.assertRegex(session.id, SESSION_ID_RE)
        self.assertEqual(session.title, DEFAULT_TITLE)
        self.assertEqual(session.messages, [])
        path = self.root / f"{session.id}.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["id"], session.id)

    def test_title_is_cleaned_and_truncated(self):
        session = self.store.create("  a\n b \x00 " + "x" * 80)
        self.assertTrue(session.title.startswith("a b"))
        self.assertLessEqual(len(session.title), 60)
        self.assertNotIn("\n", session.title)

    def test_save_and_load_message_roundtrip(self):
        session = self.store.create("工作")
        session.messages = [
            HumanMessage(content="读文件"),
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "a.txt"}, "id": "call-1"}],
            ),
            ToolMessage(content="[文件] a.txt", tool_call_id="call-1"),
            AIMessage(content="读完了"),
        ]
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human", "ai", "tool", "ai"])
        self.assertEqual(loaded.messages[0].content, "读文件")
        self.assertEqual(loaded.messages[1].tool_calls[0]["name"], "read_file")
        self.assertEqual(loaded.messages[2].tool_call_id, "call-1")
        self.assertEqual(loaded.updated_at, session.updated_at)

    def test_system_message_not_persisted(self):
        session = self.store.create()
        session.messages = [SystemMessage(content="旧系统提示"), HumanMessage(content="你好")]
        self.store.save(session)
        raw = json.loads((self.root / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertNotIn("system", [item["type"] for item in raw["messages"]])
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human"])

    def test_list_sessions_sorted_by_updated_at(self):
        first = self.store.create("A")
        first.messages = [HumanMessage(content="a")]
        self.store.save(first)
        second = self.store.create("B")
        second.messages = [HumanMessage(content="b1"), HumanMessage(content="b2")]
        self.store.save(second)
        metas = self.store.list_sessions()
        self.assertEqual([m.id for m in metas], [second.id, first.id])
        self.assertEqual(metas[0].message_count, 2)
        self.assertEqual(metas[1].title, "A")

    def test_active_pointer_set_get_clear(self):
        session = self.store.create()
        self.store.set_active_id(session.id)
        self.assertEqual(self.store.get_active_id(), session.id)
        self.store.set_active_id(None)
        self.assertIsNone(self.store.get_active_id())
        with self.assertRaises(SessionStoreError):
            self.store.set_active_id("../bad")

    def test_delete_session_and_clear_active(self):
        session = self.store.create()
        self.store.set_active_id(session.id)
        self.store.delete(session.id)
        self.assertFalse((self.root / f"{session.id}.json").exists())
        self.assertIsNone(self.store.get_active_id())
        with self.assertRaises(SessionStoreError):
            self.store.delete(session.id)

    def test_invalid_session_id_rejected(self):
        with self.assertRaises(SessionStoreError):
            self.store.load("../outside")
        with self.assertRaises(SessionStoreError):
            self.store.load("20261023-190000-zzzz")

    def test_corrupt_file_skipped_by_list(self):
        bad = self.root / "20261023-190000-dead.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.list_sessions(), [])
        with self.assertRaises(SessionStoreError):
            self.store.load("20261023-190000-dead")

    def test_load_active_or_create_falls_back_to_newest(self):
        first = self.store.create("A")
        second = self.store.create("B")
        self.store.set_active_id(first.id)
        self.store.delete(first.id)
        session = self.store.load_active_or_create()
        self.assertEqual(session.id, second.id)
        self.assertEqual(self.store.get_active_id(), second.id)

    def test_empty_store_creates_active_session(self):
        session = self.store.load_active_or_create()
        self.assertTrue((self.root / f"{session.id}.json").exists())
        self.assertEqual(self.store.get_active_id(), session.id)

    def test_atomic_write_failure_preserves_original(self):
        session = self.store.create("A")
        path = self.root / f"{session.id}.json"
        original = path.read_text(encoding="utf-8")
        session.messages = [HumanMessage(content="new")]
        with patch("session_store.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(SessionStoreError):
                self.store.save(session)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_clean_title_handles_control_characters(self):
        self.assertEqual(clean_title("  a\n\tb \x00 "), "a b")
```

- [ ] **Step 2: 运行测试，确认失败**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_store.py
```

Expected: 失败，`ModuleNotFoundError: No module named 'session_store'`。

- [ ] **Step 3: 写最小实现**

```python
# session_store.py
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)

SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
SCHEMA_VERSION = 1
DEFAULT_TITLE = "新会话"
MAX_TITLE_LENGTH = 60


class SessionStoreError(Exception):
    """会话存储错误。"""


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[BaseMessage] = field(default_factory=list)


@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def clean_title(value: str, limit: int = MAX_TITLE_LENGTH) -> str:
    """去掉控制字符、压缩空白并截断标题；空标题回退到“新会话”。"""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split())
    return text[:limit] or DEFAULT_TITLE


class SessionStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None):
        self.root = Path(root)
        self._clock = clock or _now
        self.warnings: list[str] = []

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise SessionStoreError(f"非法会话 ID: {session_id!r}")

    def _session_path(self, session_id: str) -> Path:
        self._validate_id(session_id)
        self._ensure_root()
        root = self.root.resolve()
        path = (self.root / f"{session_id}.json").resolve()
        if path.parent != root:
            raise SessionStoreError(f"会话路径越界: {session_id!r}")
        return path

    def _active_path(self) -> Path:
        self._ensure_root()
        return self.root / "active.json"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

    def create(self, title: str = DEFAULT_TITLE) -> Session:
        self._ensure_root()
        now = self._clock()
        for _ in range(100):
            session_id = now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            if not self._session_path(session_id).exists():
                break
        else:
            raise SessionStoreError("无法生成唯一会话 ID。")
        session = Session(
            id=session_id,
            title=clean_title(title),
            created_at=_iso(now),
            updated_at=_iso(now),
            messages=[],
        )
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        self._validate_id(session.id)
        now = self._clock()
        session.title = clean_title(session.title)
        session.updated_at = _iso(now)
        if not session.created_at:
            session.created_at = session.updated_at
        messages = [m for m in session.messages if not isinstance(m, SystemMessage)]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": messages_to_dict(messages),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        try:
            self._atomic_write(self._session_path(session.id), text)
        except OSError as exc:
            raise SessionStoreError(f"保存会话失败: {session.id} ({exc})") from exc

    def load(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionStoreError(f"会话不存在: {session_id}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionStoreError(f"读取会话失败: {session_id} ({exc})") from exc
        try:
            if not isinstance(raw, dict):
                raise ValueError("顶层必须是对象")
            if raw.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("不支持的 schema_version")
            if raw.get("id") != session_id:
                raise ValueError("会话 ID 不一致")
            title = clean_title(raw["title"])
            created_at = str(raw["created_at"])
            updated_at = str(raw["updated_at"])
            messages = messages_from_dict(raw["messages"])
            if not isinstance(messages, list):
                raise ValueError("messages 不是列表")
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionStoreError(f"会话文件损坏: {session_id} ({exc})") from exc
        return Session(session_id, title, created_at, updated_at, messages)

    def list_sessions(self) -> list[SessionMeta]:
        if not self.root.exists():
            return []
        metas: list[SessionMeta] = []
        for path in self.root.glob("*.json"):
            if path.name == "active.json":
                continue
            try:
                session = self.load(path.stem)
            except SessionStoreError as exc:
                self.warnings.append(str(exc))
                continue
            metas.append(
                SessionMeta(
                    id=session.id,
                    title=session.title,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    message_count=len(session.messages),
                )
            )
        metas.sort(key=lambda meta: (meta.updated_at, meta.id), reverse=True)
        return metas

    def delete(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            raise SessionStoreError(f"会话不存在: {session_id}")
        try:
            path.unlink()
        except OSError as exc:
            raise SessionStoreError(f"删除会话失败: {session_id} ({exc})") from exc
        if self.get_active_id() == session_id:
            self.set_active_id(None)

    def get_active_id(self) -> str | None:
        path = self._active_path()
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.warnings.append(f"active.json 损坏: {exc}")
            return None
        session_id = raw.get("active_id") if isinstance(raw, dict) else None
        if session_id is None:
            return None
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            self.warnings.append("active.json 无效，已忽略。")
            return None
        return session_id

    def set_active_id(self, session_id: str | None) -> None:
        if session_id is not None:
            self._validate_id(session_id)
        payload = json.dumps({"active_id": session_id}, ensure_ascii=False) + "\n"
        try:
            self._atomic_write(self._active_path(), payload)
        except OSError as exc:
            raise SessionStoreError(f"保存 active 会话失败: {exc}") from exc

    def load_active_or_create(self) -> Session:
        active_id = self.get_active_id()
        if active_id is not None:
            try:
                return self.load(active_id)
            except SessionStoreError as exc:
                self.warnings.append(str(exc))
        metas = self.list_sessions()
        if metas:
            session = self.load(metas[0].id)
            self.set_active_id(session.id)
            return session
        session = self.create()
        self.set_active_id(session.id)
        return session

    def pop_warnings(self) -> list[str]:
        warnings = self.warnings
        self.warnings = []
        return warnings
```

- [ ] **Step 4: 运行测试，确认全部通过**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_store.py
```

Expected: 13 个测试全部通过。

- [ ] **Step 5: 只提交这两个文件**

```powershell
cd D:\Vibecoding
git add Langgraph/session_store.py Langgraph/test_session_store.py
git diff --cached --name-only
git commit -m "feat: add session store"
```

Expected staged files 只有这两个；不要 `git add .`。

---

### Task 2: 命令解析、列表与历史显示

**Files:**
- Create: `D:\Vibecoding\Langgraph\session_cli.py`
- Test: `D:\Vibecoding\Langgraph\test_session_cli.py`

- [ ] **Step 1: 先写失败测试**

```python
# test_session_cli.py
from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from session_cli import (
    SessionCommandError,
    confirm,
    format_history,
    format_session_list,
    parse_command,
    resolve_session_target,
)
from session_store import Session, SessionMeta


class SessionCliTests(unittest.TestCase):
    def setUp(self):
        self.metas = [
            SessionMeta("20261023-190000-bbbb", "第二个", "2026-10-23T19:00:00+08:00", "2026-10-23T19:10:00+08:00", 4),
            SessionMeta("20261023-180000-aaaa", "第一个", "2026-10-23T18:00:00+08:00", "2026-10-23T18:10:00+08:00", 2),
        ]

    def test_parse_command(self):
        self.assertIsNone(parse_command("你好"))
        self.assertEqual(parse_command("/new 工作 记录"), ("new", "工作 记录"))
        self.assertEqual(parse_command("/sessions"), ("list", ""))
        self.assertEqual(parse_command("/HISTORY 2"), ("history", "2"))
        self.assertEqual(parse_command("/wat"), ("unknown", "/wat"))

    def test_resolve_session_target(self):
        self.assertEqual(resolve_session_target("1", self.metas), self.metas[0].id)
        self.assertEqual(resolve_session_target(self.metas[1].id, self.metas), self.metas[1].id)
        self.assertEqual(resolve_session_target("20261023-1800", self.metas), self.metas[1].id)
        with self.assertRaises(SessionCommandError):
            resolve_session_target("9", self.metas)
        with self.assertRaises(SessionCommandError):
            resolve_session_target("20261023", self.metas)

    def test_format_session_list_marks_active(self):
        text = format_session_list(self.metas, self.metas[0].id)
        self.assertIn("*", text)
        self.assertIn("1.", text)
        self.assertIn("第二个", text)
        self.assertIn("4 条", text)

    def test_format_empty_history(self):
        session = Session("20261023-190000-aaaa", "空会话", "", "", [])
        text = format_history(session)
        self.assertIn("[历史] 空会话", text)
        self.assertIn("还没有消息", text)

    def test_format_history_pages(self):
        messages = [HumanMessage(content=f"m{i}") for i in range(21)]
        session = Session("20261023-190000-aaaa", "分页", "", "", messages)
        first = format_history(session, page=1)
        second = format_history(session, page=2)
        self.assertIn("第 1/2 页（最新）", first)
        self.assertIn("m20", first)
        self.assertNotIn("m0", first)
        self.assertIn("第 2/2 页（较早）", second)
        self.assertIn("m0", second)

    def test_format_history_redacts_file_tool_content(self):
        messages = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_file",
                    "args": {"path": "D:\\secret.txt", "content": "TOP_SECRET"},
                    "id": "call-1",
                }],
            ),
            ToolMessage(content="[新建] D:\\secret.txt", tool_call_id="call-1"),
        ]
        session = Session("20261023-190000-aaaa", "脱敏", "", "", messages)
        text = format_history(session)
        self.assertIn("D:\\secret.txt", text)
        self.assertNotIn("TOP_SECRET", text)

    def test_format_history_truncates(self):
        messages = [HumanMessage(content="x" * 900)]
        session = Session("20261023-190000-aaaa", "截断", "", "", messages)
        text = format_history(session)
        self.assertIn("已截断", text)

    def test_confirm(self):
        self.assertTrue(confirm("?", lambda _: "y"))
        self.assertFalse(confirm("?", lambda _: ""))
        self.assertFalse(confirm("?", lambda _: (_ for _ in ()).throw(EOFError())))
```

- [ ] **Step 2: 运行测试，确认失败**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_cli.py
```

Expected: `ModuleNotFoundError: No module named 'session_cli'`。

- [ ] **Step 3: 写实现**

```python
# session_cli.py
from __future__ import annotations

import math
from typing import Callable

from langchain_core.messages import BaseMessage

from session_store import Session, SessionMeta

HISTORY_PAGE_SIZE = 20

FILE_TOOL_ACTIONS = {
    "write_file": "写入",
    "edit_file": "修改",
    "write_docx": "写入",
    "edit_docx": "修改",
    "write_xlsx": "写入",
    "edit_xlsx": "修改",
    "write_pptx": "写入",
    "edit_pptx": "修改",
}

KNOWN_COMMANDS = {"new", "list", "sessions", "switch", "delete", "clear", "history", "help"}

HELP_TEXT = """会话命令:
  /new [标题]          新建并切换；标题可省略
  /list                列出会话；/sessions 同义
  /switch <编号|ID>    切换；编号来自 /list
  /delete <编号|ID>    删除；先 y/N 确认
  /clear               清空当前会话历史；先 y/N 确认
  /history [页码]      查看当前会话历史；默认第 1 页
  /help                显示本帮助
输入 exit / quit / q / 退出 结束。"""


class SessionCommandError(Exception):
    """会话命令错误。"""


def parse_command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].strip()
    if not body:
        return ("unknown", stripped)
    name, _, arg = body.partition(" ")
    name = name.lower()
    if name not in KNOWN_COMMANDS:
        return ("unknown", stripped)
    if name == "sessions":
        name = "list"
    return name, arg.strip()


def resolve_session_target(target: str, metas: list[SessionMeta]) -> str:
    raw = target.strip()
    if not raw:
        raise SessionCommandError("请提供会话编号或 ID。")
    if raw.isdigit():
        index = int(raw)
        if index < 1 or index > len(metas):
            raise SessionCommandError(f"编号超出范围: {index}（共 {len(metas)} 个）")
        return metas[index - 1].id
    exact = [meta.id for meta in metas if meta.id == raw]
    if exact:
        return exact[0]
    matches = [meta.id for meta in metas if meta.id.startswith(raw)]
    if not matches:
        raise SessionCommandError(f"找不到会话: {raw}")
    if len(matches) > 1:
        raise SessionCommandError(f"会话 ID 前缀不唯一: {raw}")
    return matches[0]


def format_session_list(metas: list[SessionMeta], active_id: str | None) -> str:
    if not metas:
        return "[会话] 还没有任何会话。"
    lines = ["会话列表（* 当前）:"]
    for index, meta in enumerate(metas, 1):
        marker = "*" if meta.id == active_id else " "
        stamp = meta.updated_at.replace("T", " ")[:16]
        lines.append(
            f" {marker} {index:>2}. {meta.id}  {meta.title}  "
            f"({meta.message_count} 条, {stamp})"
        )
    return "\n".join(lines)


def _text_content(content: object) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…（已截断）"


def _format_tool_call(call: dict) -> str:
    name = str(call.get("name", "unknown"))
    args = call.get("args", {})
    if name in FILE_TOOL_ACTIONS:
        path = args.get("path", "?") if isinstance(args, dict) else "?"
        return f"[工具] {name}(path={path!r})"
    return f"[工具] {name}({_truncate(str(args), 120)})"


def format_history(session: Session, page: int = 1, page_size: int = HISTORY_PAGE_SIZE) -> str:
    lines = [f"[历史] {session.title} ({session.id})"]
    total = len(session.messages)
    if total == 0:
        lines.append("当前会话还没有消息。")
        return "\n".join(lines)
    pages = math.ceil(total / page_size)
    if page < 1 or page > pages:
        raise SessionCommandError(f"页码超出范围: {page}（共 {pages} 页）")
    label = "最新" if page == 1 else "较早"
    lines.append(f"第 {page}/{pages} 页（{label}） | 共 {total} 条消息")
    lines.append("")
    end = total - (page - 1) * page_size
    start = max(0, end - page_size)
    for message in session.messages[start:end]:
        message_type = getattr(message, "type", "")
        content = _text_content(getattr(message, "content", ""))
        if message_type == "human":
            lines.append(f"[用户] {_truncate(content, 800) or '（空）'}")
        elif message_type == "ai":
            text = content.strip()
            tool_calls = getattr(message, "tool_calls", None) or []
            if text:
                lines.append(f"[助手] {_truncate(text, 800)}")
            for call in tool_calls:
                lines.append(_format_tool_call(call))
            if not text and not tool_calls:
                lines.append("[助手] （无文本）")
        elif message_type == "tool":
            flat = content.replace("\r\n", "\n").replace("\n", " | ").strip()
            lines.append(f"[工具结果] {_truncate(flat, 200)}")
    return "\n".join(lines)


def confirm(prompt: str, input_fn: Callable[[str], str] = input) -> bool:
    try:
        answer = input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"y", "yes", "是"}
```

- [ ] **Step 4: 运行测试，确认通过**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_cli.py
```

Expected: 8 个测试全部通过。

- [ ] **Step 5: 只提交这两个文件**

```powershell
cd D:\Vibecoding
git add Langgraph/session_cli.py Langgraph/test_session_cli.py
git diff --cached --name-only
git commit -m "feat: add session commands and history display"
```

---

### Task 3: 接入 `graph_agent.py` 主循环

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py`
- Modify/Create: `D:\Vibecoding\Langgraph\.gitignore`
- Test: `D:\Vibecoding\Langgraph\test_session_flow.py`

- [ ] **Step 1: 先写主循环集成测试**

```python
# test_session_flow.py
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage

from graph_agent import run_chat_loop
from session_store import SessionStore


class FakeGraph:
    def invoke(self, state, config):
        messages = list(state["messages"])
        messages.append(AIMessage(content="收到"))
        return {"messages": messages}


class SessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_flow_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SessionStore(self.root)
        self.session = self.store.create("新会话")
        self.outputs: list[str] = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, inputs: list[str]) -> None:
        iterator = iter(inputs)
        run_chat_loop(
            self.store,
            FakeGraph(),
            self.session,
            input_fn=lambda prompt: next(iterator),
            output=self.outputs.append,
        )

    def test_chat_saves_history_and_auto_title(self):
        self._run(["你好", "exit"])
        loaded = self.store.load(self.session.id)
        self.assertEqual(loaded.title, "你好")
        self.assertEqual([m.type for m in loaded.messages], ["human", "ai"])
        self.assertEqual(loaded.messages[0].content, "你好")
        self.assertIn("[助手] 收到", "\n".join(self.outputs))

    def test_new_command_creates_and_switches(self):
        self._run(["/new 工作", "exit"])
        metas = self.store.list_sessions()
        self.assertEqual(len(metas), 2)
        self.assertEqual(self.store.get_active_id(), metas[0].id)
        self.assertEqual(metas[0].title, "工作")
        self.assertIn("已新建", "\n".join(self.outputs))

    def test_delete_current_falls_back(self):
        second = self.store.create("B")
        self.store.set_active_id(second.id)
        self.session = second
        self._run(["/delete 1", "y", "exit"])
        metas = self.store.list_sessions()
        self.assertEqual(len(metas), 1)
        self.assertEqual(self.store.get_active_id(), metas[0].id)
        self.assertEqual(metas[0].title, "新会话")

    def test_history_command_after_chat(self):
        self._run(["你好", "/history", "exit"])
        output = "\n".join(self.outputs)
        self.assertIn("[历史]", output)
        self.assertIn("[用户] 你好", output)
```

- [ ] **Step 2: 运行，确认失败**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_flow.py
```

Expected: `ImportError: cannot import name 'run_chat_loop'`。

- [ ] **Step 3: 修改 `graph_agent.py`**

在现有 import 区（`from langgraph.prebuilt ...` 之后）加入：

```python
from typing import Callable

from session_cli import (
    FILE_TOOL_ACTIONS,
    HELP_TEXT,
    SessionCommandError,
    confirm,
    format_history,
    format_session_list,
    parse_command,
    resolve_session_target,
)
from session_store import (
    DEFAULT_TITLE,
    Session,
    SessionStore,
    SessionStoreError,
    clean_title,
)
```

删除 `graph_agent.py:743-756` 的 `_FILE_TOOL_ACTIONS` 定义（改为使用 `session_cli.FILE_TOOL_ACTIONS`）；保留 `EXIT_COMMANDS`，删除 `CLEAR_COMMANDS`（`/clear` 由命令分发处理）。

把 `_print_tool_calls` 改为接收输出回调：

```python
def _print_tool_calls(messages: list, start: int, output=print) -> None:
    """打印本轮工具调用；写文件只显示路径，不把文件内容打到终端。"""
    tool_results = {}
    for message in messages[start:]:
        if getattr(message, "type", None) == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if call_id:
                tool_results[call_id] = str(message.content)

    for message in messages[start:]:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name", "unknown")
            args = call.get("args", {})
            action = FILE_TOOL_ACTIONS.get(name)
            if action is None:
                output(f"[工具] {name}({args})")
                continue

            path = args.get("path", "?") if isinstance(args, dict) else "?"
            result = tool_results.get(call.get("id"), "")
            if result.startswith("[错误]"):
                output(f"[失败] {action}文件: {path}")
            elif result.startswith("[提示]"):
                output(f"[提示] 文件未变化: {path}")
            else:
                output(f"[完成] 已{action}: {path}")


def _print_reply(message, output=print) -> None:
    content = message.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content).strip()
    output(f"助手: {text or '（模型没有返回文本）'}")
```

用下面的 `run_chat_loop` 和 `main` 替换原 `graph_agent.py:799-846`：

```python
def run_chat_loop(
    store: SessionStore,
    graph,
    session: Session,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    """终端主循环；抽出来便于用 fake graph / 临时 store 测试。"""
    messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]

    while True:
        try:
            raw = input_fn("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            try:
                store.save(session)
            except SessionStoreError as exc:
                output(f"[错误] {exc}")
            output("\n再见。")
            break

        if not raw:
            continue

        parsed = parse_command(raw)
        if parsed is not None:
            name, arg = parsed
            try:
                if name == "help":
                    output(HELP_TEXT)
                elif name == "list":
                    output(format_session_list(store.list_sessions(), session.id))
                    for warning in store.pop_warnings():
                        output(f"[提示] {warning}")
                elif name == "history":
                    try:
                        page = int(arg) if arg else 1
                    except ValueError as exc:
                        raise SessionCommandError("页码必须是正整数。") from exc
                    output(format_history(session, page))
                elif name == "clear":
                    if not confirm("确认清空当前会话历史？[y/N] ", input_fn):
                        output("已取消。")
                        continue
                    session.messages = []
                    store.save(session)
                    messages = [SystemMessage(content=SYSTEM_PROMPT)]
                    output("已清空当前会话历史。")
                elif name == "new":
                    store.save(session)
                    session = store.create(arg or DEFAULT_TITLE)
                    store.set_active_id(session.id)
                    messages = [SystemMessage(content=SYSTEM_PROMPT)]
                    output(f"[会话] 已新建: {session.title} ({session.id})")
                elif name == "switch":
                    if not arg:
                        raise SessionCommandError("用法: /switch <编号|ID>")
                    metas = store.list_sessions()
                    target_id = resolve_session_target(arg, metas)
                    if target_id == session.id:
                        output("已经是当前会话。")
                        continue
                    store.save(session)
                    session = store.load(target_id)
                    store.set_active_id(session.id)
                    messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]
                    output(
                        f"[会话] 已切换到: {session.title} ({session.id}) | "
                        f"共 {len(session.messages)} 条消息"
                    )
                elif name == "delete":
                    if not arg:
                        raise SessionCommandError("用法: /delete <编号|ID>")
                    metas = store.list_sessions()
                    target_id = resolve_session_target(arg, metas)
                    target_meta = next((m for m in metas if m.id == target_id), None)
                    if target_meta is None:
                        raise SessionCommandError(f"找不到会话: {target_id}")
                    if not confirm(
                        f"确认删除会话 '{target_meta.title}' ({target_id})？[y/N] ",
                        input_fn,
                    ):
                        output("已取消。")
                        continue
                    was_current = target_id == session.id
                    store.delete(target_id)
                    if was_current:
                        metas = store.list_sessions()
                        if metas:
                            session = store.load(metas[0].id)
                            store.set_active_id(session.id)
                            messages = [
                                SystemMessage(content=SYSTEM_PROMPT),
                                *session.messages,
                            ]
                            output(
                                f"[会话] 已删除当前会话，切换到: "
                                f"{session.title} ({session.id})"
                            )
                        else:
                            session = store.create(DEFAULT_TITLE)
                            store.set_active_id(session.id)
                            messages = [SystemMessage(content=SYSTEM_PROMPT)]
                            output("[会话] 已删除当前会话，已新建空会话。")
                    else:
                        output(f"[会话] 已删除: {target_meta.title} ({target_id})")
                else:
                    output("[命令] 未知命令，输入 /help 查看。")
            except (SessionStoreError, SessionCommandError, OSError) as exc:
                output(f"[错误] {exc}")
            continue

        if raw.lower() in EXIT_COMMANDS:
            try:
                store.save(session)
            except SessionStoreError as exc:
                output(f"[错误] {exc}")
            output("再见。")
            break

        messages.append(HumanMessage(content=raw))
        start = len(messages) - 1
        try:
            result = graph.invoke(
                {"messages": messages},
                config={"recursion_limit": 25},
            )
        except KeyboardInterrupt:
            messages.pop()
            output("\n[已中断本次请求]")
            continue
        except Exception as exc:
            messages.pop()
            output(f"[错误] 调用模型失败: {exc}")
            continue

        result_messages = result["messages"]
        session.messages = [
            message
            for message in result_messages
            if not isinstance(message, SystemMessage)
        ]
        if session.title == DEFAULT_TITLE:
            first_user = next(
                (m for m in session.messages if isinstance(m, HumanMessage)),
                None,
            )
            if first_user is not None:
                session.title = clean_title(str(first_user.content)[:30])
        try:
            store.save(session)
        except SessionStoreError as exc:
            output(f"[错误] 保存会话失败: {exc}")
        messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]
        _print_tool_calls(result_messages, start, output)
        _print_reply(result_messages[-1], output)


def main() -> None:
    provider, model, base_url = active_model()
    print(f"[LLM] provider={provider} | model={model} | base_url={base_url}")

    store = SessionStore(Path(__file__).resolve().parent / ".sessions")
    try:
        session = store.load_active_or_create()
    except SessionStoreError as exc:
        print(f"[错误] {exc}")
        return
    for warning in store.pop_warnings():
        print(f"[提示] {warning}")
    print(
        f"[会话] {session.title} ({session.id}) | "
        f"共 {len(session.messages)} 条消息"
    )
    print("输入 /help 查看会话命令；输入 exit / quit / q / 退出 结束。")

    graph = build_graph()
    run_chat_loop(store, graph, session)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 创建 `.gitignore`**

```text
.sessions/
```

如果文件已存在，只在末尾追加这一行，不要覆盖其他规则。

- [ ] **Step 5: 运行集成测试**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_flow.py
```

Expected: 4 个测试全部通过。

- [ ] **Step 6: 只提交四个文件**

```powershell
cd D:\Vibecoding
git add Langgraph/graph_agent.py Langgraph/.gitignore Langgraph/test_session_flow.py
git diff --cached --name-only
git commit -m "feat: add session management to chat cli"
```

---

### Task 4: 全量验证与手工冒烟

**Files:**
- Test: `D:\Vibecoding\Langgraph\test_session_store.py`、`test_session_cli.py`、`test_session_flow.py`

- [ ] **Step 1: 跑新增测试**

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_store.py test_session_cli.py test_session_flow.py
```

Expected: 25 个新增测试全部通过。

- [ ] **Step 2: 跑全量测试**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest discover -v
```

Expected: 现有 70 个 + 新增 25 个，共 95 个测试通过。

- [ ] **Step 3: 手工冒烟**

```powershell
D:\Anaconda3\envs\rag\python.exe graph_agent.py
```

依次执行：

```text
/new 测试会话
你好
/history
/list
/new 第二个会话
你好
/list
/switch 2
/history
exit
```

再次启动 `graph_agent.py`，确认：

- 自动恢复上次 active 会话；
- `/list` 有两个会话；
- `/history` 能分页显示；
- `/delete` 删除当前会话后会切到剩余会话；
- `D:\Vibecoding\Langgraph\.sessions\` 下是 JSON 文件，且 `git status` 不显示它。

- [ ] **Step 4: 检查没有误提交聊天内容**

```powershell
cd D:\Vibecoding
git status --short
```

Expected: 不出现 `Langgraph/.sessions/`；只允许出现用户原有的其他未跟踪文件。

---

## Self-Review

- **Spec coverage:** 会话持久化、列表、切换、删除、清空、历史分页、active 回退、错误处理、标题、`.gitignore`、测试均有对应任务。
- **No placeholders:** 所有代码、测试、命令、期望结果已写出。
- **Type consistency:** `SessionStore` / `Session` / `SessionMeta` / `SessionCommandError` 的签名在三个任务中一致；`run_chat_loop` 的 `input_fn`/`output` 与测试一致；`FILE_TOOL_ACTIONS` 只由 `session_cli.py` 定义。
- **Existing behavior:** `build_graph()`、`TOOLS`、`office_tools.py` 不改；70 个现有测试作为回归门槛。
