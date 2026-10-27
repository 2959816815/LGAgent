r"""session_store_sqlite.py — SQLite 会话持久化后端。

与 `session_store.SessionStore` 暴露相同的最小接口，供桌面端切换后端：

    create(title, model_key) -> Session
    save(session) -> None
    load(session_id) -> Session
    list_sessions() -> list[SessionMeta]
    delete(session_id) -> None
    get_active_id() -> str | None
    set_active_id(session_id | None) -> None
    load_active_or_create() -> Session
    pop_warnings() -> list[str]

首次打开空库时，如果同目录存在旧版 `.sessions/`，会自动导入旧 JSON 会话。
导入只做一次，之后以 SQLite 为准。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from langchain_core.messages import BaseMessage, SystemMessage, messages_from_dict, messages_to_dict

from session_store import (
    DEFAULT_TITLE,
    SCHEMA_VERSION,
    SESSION_ID_RE,
    Session,
    SessionMeta,
    SessionStore,
    SessionStoreError,
    _iso,
    _now,
    clean_title,
    sanitize_messages,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    model_key     TEXT,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class SqliteSessionStore:
    def __init__(
        self,
        db_path: Path,
        legacy_dir: Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db_path = Path(db_path)
        self.legacy_dir = Path(legacy_dir) if legacy_dir is not None else None
        self._clock = clock or _now
        self.warnings: list[str] = []
        self._ensure_parent()
        self._init_schema()
        self._migrate_legacy_once()

    def _ensure_parent(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise SessionStoreError(f"不支持的数据库版本: {version}")

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise SessionStoreError(f"非法会话 ID: {session_id!r}")

    def _migrate_legacy_once(self) -> None:
        if self.legacy_dir is None or not self.legacy_dir.is_dir():
            return
        with self._connect() as connection:
            done = connection.execute(
                "SELECT value FROM state WHERE key = 'legacy_json_migrated'"
            ).fetchone()
            if done is not None:
                return
            has_sessions = connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        if has_sessions is not None:
            self._set_state("legacy_json_migrated", "1")
            return

        legacy = SessionStore(self.legacy_dir, clock=self._clock)
        migrated = 0
        for meta in legacy.list_sessions():
            try:
                session = legacy.load(meta.id)
            except SessionStoreError as exc:
                self.warnings.append(str(exc))
                continue
            self._insert_session(session, touch=False)
            migrated += 1
        active_id = legacy.get_active_id()
        if active_id is not None:
            try:
                self.set_active_id(active_id)
            except SessionStoreError:
                pass
        self._set_state("legacy_json_migrated", "1")
        if migrated:
            self.warnings.append(f"已从旧 JSON 会话导入 {migrated} 个会话。")

    def _set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _get_state(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def _insert_session(self, session: Session, touch: bool = True) -> None:
        self._validate_id(session.id)
        now = self._clock()
        session.title = clean_title(session.title)
        if touch:
            session.updated_at = _iso(now)
        if not session.created_at:
            session.created_at = session.updated_at
        messages = sanitize_messages(
            [message for message in session.messages if not isinstance(message, SystemMessage)]
        )
        session.messages = messages
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at, model_key, message_count) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, created_at=excluded.created_at, "
                "updated_at=excluded.updated_at, model_key=excluded.model_key, "
                "message_count=excluded.message_count",
                (
                    session.id,
                    session.title,
                    session.created_at,
                    session.updated_at,
                    session.model_key,
                    len(messages),
                ),
            )
            connection.execute("DELETE FROM messages WHERE session_id = ?", (session.id,))
            connection.executemany(
                "INSERT INTO messages(session_id, seq, payload) VALUES(?, ?, ?)",
                [
                    (
                        session.id,
                        index,
                        json.dumps(messages_to_dict([message])[0], ensure_ascii=False),
                    )
                    for index, message in enumerate(messages)
                ],
            )

    def create(
        self,
        title: str = DEFAULT_TITLE,
        model_key: str | None = None,
    ) -> Session:
        now = self._clock()
        for _ in range(100):
            session_id = now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
            if exists is None:
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
        self._insert_session(session, touch=False)
        return session

    def save(self, session: Session) -> None:
        self._insert_session(session, touch=True)

    def load(self, session_id: str) -> Session:
        self._validate_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, title, created_at, updated_at, model_key "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionStoreError(f"会话不存在: {session_id}")
            message_rows = connection.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        try:
            messages: list[BaseMessage] = []
            for (payload,) in message_rows:
                messages.extend(messages_from_dict([json.loads(payload)]))
            messages = sanitize_messages(messages)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SessionStoreError(f"会话数据损坏: {session_id} ({exc})") from exc
        return Session(
            id=row[0],
            title=clean_title(row[1]),
            created_at=row[2],
            updated_at=row[3],
            messages=messages,
            model_key=row[4],
        )

    def list_sessions(self) -> list[SessionMeta]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, created_at, updated_at, message_count, model_key "
                "FROM sessions ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [
            SessionMeta(
                id=row[0],
                title=clean_title(row[1]),
                created_at=row[2],
                updated_at=row[3],
                message_count=int(row[4]),
                model_key=row[5],
            )
            for row in rows
        ]

    def delete(self, session_id: str) -> None:
        self._validate_id(session_id)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise SessionStoreError(f"会话不存在: {session_id}")
        if self.get_active_id() == session_id:
            self.set_active_id(None)

    def get_active_id(self) -> str | None:
        active_id = self._get_state("active_id")
        if active_id is None:
            return None
        if not SESSION_ID_RE.fullmatch(active_id):
            self.warnings.append("active 会话 ID 无效，已忽略。")
            return None
        return active_id

    def set_active_id(self, session_id: str | None) -> None:
        if session_id is not None:
            self._validate_id(session_id)
        self._set_state("active_id", session_id or "")

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
