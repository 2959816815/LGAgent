r"""session_store.py — 会话的 JSON 文件持久化层。

每个会话一个 JSON 文件（`.sessions/` 下），另用 `active.json` 记录当前会话。
本模块只负责数据与磁盘读写，不做任何终端输入输出。

运行测试：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_store.py
"""

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
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
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
    model_key: str | None = None
    # 较早对话的摘要（压缩后 messages 只保留最近的若干轮）
    summary: str = ""
    # 已经被压缩进摘要的历史消息条数，仅用于界面提示
    summary_count: int = 0
    # 摘要覆盖到的消息条数：summary 描述 messages[:summary_upto]
    summary_upto: int = 0
    summary_updated_at: str = ""


@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    model_key: str | None = None


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def clean_title(value: str, limit: int = MAX_TITLE_LENGTH) -> str:
    """去掉控制字符、压缩空白并截断标题；空标题回退到“新会话”。"""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split())
    return text[:limit] or DEFAULT_TITLE


def sanitize_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """移除不完整的工具调用序列。

    OpenAI/DeepSeek 要求每个带 tool_calls 的 assistant 消息后面紧跟
    对应 tool_call_id 的 tool 消息。流式生成被中断、取消或工具执行失败时，
    历史里可能只留下 assistant.tool_calls，下一次请求就会返回 400。
    这里把这类半截序列清理掉：保留 assistant 的文本内容，去掉 tool_calls；
    没有文本的 assistant 直接丢弃；孤立的 tool 消息也丢弃。
    """
    clean: list[BaseMessage] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]

        if isinstance(message, ToolMessage):
            # 没有紧邻的 assistant.tool_calls，孤立工具结果无法发送给模型。
            index += 1
            continue

        if isinstance(message, AIMessage) and message.tool_calls:
            call_ids = [
                str(call.get("id"))
                for call in message.tool_calls
                if call.get("id") is not None
            ]
            cursor = index + 1
            tool_messages: dict[str, ToolMessage] = {}
            while cursor < total and isinstance(messages[cursor], ToolMessage):
                tool_message = messages[cursor]
                tool_messages[str(tool_message.tool_call_id)] = tool_message
                cursor += 1

            complete = bool(call_ids) and all(
                call_id in tool_messages for call_id in call_ids
            )
            if complete:
                clean.append(message)
                clean.extend(tool_messages[call_id] for call_id in call_ids)
            elif message.content:
                clean.append(
                    AIMessage(
                        content=message.content,
                        additional_kwargs=message.additional_kwargs,
                        response_metadata=message.response_metadata,
                        id=message.id,
                        name=message.name,
                    )
                )
            index = cursor
            continue

        clean.append(message)
        index += 1

    return clean


def message_text(message) -> str:
    """把消息内容压成纯文本（多段内容只取文本部分）。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


class SessionStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None):
        self.root = Path(root)
        self._clock = clock or _now
        self.warnings: list[str] = []
        # 目录属于存储层职责：构造后调用方即可直接读写 self.root。
        self._ensure_root()

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

    def create(
        self,
        title: str = DEFAULT_TITLE,
        model_key: str | None = None,
    ) -> Session:
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
            model_key=model_key,
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
        messages = sanitize_messages(
            [m for m in session.messages if not isinstance(m, SystemMessage)]
        )
        session.messages = messages
        payload = {
            "schema_version": SCHEMA_VERSION,
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model_key": session.model_key,
            "summary": session.summary,
            "summary_count": session.summary_count,
            "summary_upto": session.summary_upto,
            "summary_updated_at": session.summary_updated_at,
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
            messages = sanitize_messages(messages_from_dict(raw["messages"]))
            if not isinstance(messages, list):
                raise ValueError("messages 不是列表")
            model_key = raw.get("model_key")
            if model_key is not None and not isinstance(model_key, str):
                raise ValueError("model_key 必须是字符串或 null")
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionStoreError(f"会话文件损坏: {session_id} ({exc})") from exc
        summary = raw.get("summary") or ""
        if not isinstance(summary, str):
            summary = ""
        summary_count = raw.get("summary_count") or 0
        if isinstance(summary_count, bool) or not isinstance(summary_count, int):
            summary_count = 0
        summary_upto = raw.get("summary_upto") or 0
        if isinstance(summary_upto, bool) or not isinstance(summary_upto, int):
            summary_upto = 0
        summary_upto = max(0, summary_upto)
        summary_updated_at = raw.get("summary_updated_at") or ""
        if not isinstance(summary_updated_at, str):
            summary_updated_at = ""
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
                    model_key=session.model_key,
                )
            )
        metas.sort(key=lambda meta: (meta.updated_at, meta.id), reverse=True)
        return metas

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """扫描所有会话消息做子串搜索（JSON 后端没有全文索引）。"""
        needle = (query or "").strip()
        if not needle or limit <= 0:
            return []
        lowered = needle.casefold()
        results: list[dict] = []
        for meta in self.list_sessions():
            try:
                session = self.load(meta.id)
            except SessionStoreError:
                continue
            for index, message in enumerate(session.messages):
                text = message_text(message)
                position = text.casefold().find(lowered)
                if position < 0:
                    continue
                start = max(0, position - 32)
                end = min(len(text), position + len(needle) + 64)
                snippet = (
                    ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
                )
                results.append(
                    {
                        "session_id": meta.id,
                        "message_index": index,
                        "role": getattr(message, "type", "message"),
                        "snippet": snippet,
                    }
                )
                if len(results) >= limit:
                    return results
        return results

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
