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
