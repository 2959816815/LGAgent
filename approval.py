"""approval.py — 写入前的用户审批、diff 预览与撤销。

CLI 没有 broker 时工具照旧直接执行；桌面端在 run_graph 里注入 broker，
写类工具会阻塞等待前端批准。撤销记录保存在进程内，按会话隔离。
"""

from __future__ import annotations

import difflib
import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import StructuredTool

WRITE_TOOL_NAMES = {
    "write_file",
    "edit_file",
    "write_docx",
    "edit_docx",
    "write_xlsx",
    "edit_xlsx",
    "write_pptx",
    "edit_pptx",
}

TITLES = {
    "write_file": "写入文件",
    "edit_file": "修改文件",
    "write_docx": "写入 Word 文档",
    "edit_docx": "修改 Word 文档",
    "write_xlsx": "写入 Excel 表格",
    "edit_xlsx": "修改 Excel 表格",
    "write_pptx": "写入 PPT 演示文稿",
    "edit_pptx": "修改 PPT 演示文稿",
}

_ACTIVE: "ApprovalBroker | None" = None
_ACTIVE_LOCK = threading.Lock()


class ApprovalDenied(Exception):
    """用户拒绝执行。"""


class ApprovalTimeout(Exception):
    """等待审批超时。"""


class UndoEmpty(Exception):
    """没有可撤销的写入。"""


@dataclass
class UndoEntry:
    tool: str
    path: Path
    before: bytes | None
    backup: Path | None
    created_at: float


@dataclass
class _Pending:
    event: threading.Event
    approved: bool = False


def current_broker() -> "ApprovalBroker | None":
    with _ACTIVE_LOCK:
        return _ACTIVE


def set_current(broker: "ApprovalBroker | None") -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = broker


def _target_path(args: dict) -> Path:
    raw = str(args.get("path") or "")
    return Path(raw).expanduser() if raw else Path("")


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        data = path.read_bytes()
        if b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def _unified(old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile="修改前",
        tofile="修改后",
        lineterm="",
    )
    text = "\n".join(diff)
    if len(text) > 12_000:
        text = text[:12_000] + "\n...（diff 已截断）"
    return text


def build_preview(tool: str, args: dict) -> dict:
    """给前端展示的标题、路径和 diff/参数摘要。"""
    target = _target_path(args)
    title = TITLES.get(tool, tool)
    if tool.startswith("mcp__"):
        detail = json.dumps(args, ensure_ascii=False, indent=2)[:4000]
        return {
            "tool": tool,
            "title": "MCP 工具",
            "path": "",
            "diff": "",
            "detail": detail,
        }
    if tool in {"write_file", "edit_file"}:
        old = _read_text(target) if target else None
        if tool == "write_file":
            new = str(args.get("content", ""))
        else:
            old_text = str(args.get("old_text", ""))
            new_text = str(args.get("new_text", ""))
            replace_all = bool(args.get("replace_all", False))
            new = (old or "").replace(old_text, new_text, -1 if replace_all else 1)
        diff = _unified(old or "", new)
        return {
            "tool": tool,
            "title": title,
            "path": str(target),
            "diff": diff,
            "detail": "",
        }
    detail = json.dumps(args, ensure_ascii=False, indent=2)[:4000]
    return {"tool": tool, "title": title, "path": str(target), "diff": "", "detail": detail}


def _find_backup(target: Path) -> Path | None:
    if not target.parent.is_dir():
        return None
    candidates = [p for p in target.parent.glob(target.name + ".bak*") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


class ApprovalBroker:
    def __init__(
        self,
        run_id: str,
        session_id: str,
        emit: Callable[[str, dict], None],
        undo_entries: list[UndoEntry] | None = None,
        timeout: float = 300.0,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.emit = emit
        self.timeout = timeout
        self.undo_entries = undo_entries if undo_entries is not None else []
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def request(self, tool: str, args: dict) -> None:
        preview = build_preview(tool, args)
        approval_id = f"ap-{uuid.uuid4().hex[:10]}"
        pending = _Pending(threading.Event())
        with self._lock:
            self._pending[approval_id] = pending
        self.emit(
            "approval_request",
            {
                "run_id": self.run_id,
                "session_id": self.session_id,
                "approval_id": approval_id,
                **preview,
            },
        )
        if not pending.event.wait(self.timeout):
            with self._lock:
                self._pending.pop(approval_id, None)
            raise ApprovalTimeout("等待审批超时")
        with self._lock:
            self._pending.pop(approval_id, None)
        if not pending.approved:
            raise ApprovalDenied("用户拒绝")

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return False
            pending.approved = approved
        pending.event.set()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()
        for pending in pending_items:
            pending.approved = False
            pending.event.set()

    def capture(self, tool: str, args: dict) -> UndoEntry | None:
        target = _target_path(args)
        if not str(target):
            return None
        before = _read_text(target) if tool in {"write_file", "edit_file"} else None
        before_bytes = before.encode("utf-8") if before is not None else None
        existed = target.exists()
        if not existed:
            return UndoEntry(tool=tool, path=target, before=None, backup=None, created_at=time.time())
        return UndoEntry(
            tool=tool,
            path=target,
            before=before_bytes,
            backup=None,
            created_at=time.time(),
        )

    def commit(self, tool: str, args: dict, entry: UndoEntry | None) -> None:
        if entry is None:
            return
        entry.backup = _find_backup(entry.path)
        self.undo_entries.append(entry)
        self.emit(
            "undo_available",
            {
                "run_id": self.run_id,
                "session_id": self.session_id,
                "tool": tool,
                "path": str(entry.path),
            },
        )

    def undo_last(self) -> Path:
        return undo_last(self.undo_entries)


def undo_last(entries: list[UndoEntry]) -> Path:
    if not entries:
        raise UndoEmpty("没有可撤销的写入")
    entry = entries.pop()
    if entry.backup and entry.backup.is_file():
        shutil.copy2(entry.backup, entry.path)
    elif entry.before is not None:
        entry.path.write_bytes(entry.before)
    elif entry.path.is_file():
        entry.path.unlink()
    return entry.path


def _wrap(tool: Any) -> Any:
    name = tool.name

    def run_tool(**kwargs):
        broker = current_broker()
        if broker is None:
            return tool.func(**kwargs)
        entry = None
        try:
            broker.request(name, kwargs)
            entry = broker.capture(name, kwargs)
            result = tool.func(**kwargs)
        except ApprovalDenied:
            return "[错误] 用户拒绝执行写入"
        except ApprovalTimeout:
            return "[错误] 审批超时，已取消写入"
        if str(result).startswith("[错误]"):
            return result
        broker.commit(name, kwargs, entry)
        return result

    return StructuredTool.from_function(
        func=run_tool,
        name=name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


def wrap_tools(tools: list[Any]) -> list[Any]:
    return [_wrap(tool) if tool.name in WRITE_TOOL_NAMES else tool for tool in tools]


def _wrap_mcp(tool: Any) -> Any:
    name = tool.name
    metadata = getattr(tool, "metadata", None)
    permission = "auto"
    if isinstance(metadata, dict):
        permission = str(metadata.get("permission") or "auto")

    def run_tool(**kwargs):
        broker = current_broker()
        if broker is not None and permission == "ask":
            try:
                broker.request(name, kwargs)
            except ApprovalDenied:
                return "[错误] 用户拒绝执行 MCP 工具"
            except ApprovalTimeout:
                return "[错误] 审批超时，已取消 MCP 工具"
        if permission == "deny":
            return "[错误] MCP 工具已被配置禁用"
        try:
            return tool.func(**kwargs)
        except Exception as exc:
            return f"[错误] MCP 工具失败: {exc}"

    return StructuredTool.from_function(
        func=run_tool,
        name=name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


def wrap_mcp_tools(tools: list[Any]) -> list[Any]:
    """只包装 MCP 工具，保留其他原生工具。"""
    return [
        _wrap_mcp(tool)
        if str(getattr(tool, "name", "")).startswith("mcp__")
        else tool
        for tool in tools
    ]
