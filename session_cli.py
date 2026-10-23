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

KNOWN_COMMANDS = {
    "new",
    "list",
    "sessions",
    "switch",
    "delete",
    "clear",
    "history",
    "agents",
    "help",
}

HELP_TEXT = """会话命令:
  /new [标题]          新建并切换；标题可省略
  /list                列出会话；/sessions 同义
  /switch <编号|ID>    切换；编号来自 /list
  /delete <编号|ID>    删除；先 y/N 确认
  /clear               清空当前会话历史；先 y/N 确认
  /history [页码]      查看当前会话历史；默认第 1 页
  /agents              查看子 agent 状态
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
        # Windows 路径用 !r 会把反斜杠转义成 \\，直接显示字符串更可读。
        shown = path if isinstance(path, str) else repr(path)
        return f"[工具] {name}(path={shown})"
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
