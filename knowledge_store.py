"""SQLite persistence for the local knowledge base."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,
    enabled      INTEGER NOT NULL DEFAULT 1,
    added_at     TEXT NOT NULL,
    last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    size         INTEGER NOT NULL,
    mtime        REAL NOT NULL,
    content_hash TEXT NOT NULL,
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'indexed',
    error        TEXT,
    indexed_at   TEXT,
    UNIQUE(source_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    text            TEXT NOT NULL,
    heading         TEXT,
    page            INTEGER,
    sheet           TEXT,
    slide           INTEGER,
    tokens          INTEGER NOT NULL,
    embedding       BLOB NOT NULL,
    dim             INTEGER NOT NULL,
    embedding_model TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id    UNINDEXED,
    document_id UNINDEXED,
    tokens_text,
    tokenize = 'unicode61'
);
"""


class KnowledgeError(RuntimeError):
    """Invalid knowledge-base operation."""


@dataclass(frozen=True)
class SourceRow:
    id: int
    path: str
    enabled: bool
    added_at: str
    last_scan_at: str


@dataclass(frozen=True)
class DocumentRow:
    id: int
    source_id: int
    path: str
    rel_path: str
    size: int
    mtime: float
    content_hash: str
    title: str
    status: str
    error: str
    indexed_at: str


@dataclass(frozen=True)
class ChunkInsert:
    text: str
    tokens_text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None
    tokens: int = 0
    embedding: bytes = b""
    dim: int = 0
    embedding_model: str = DEFAULT_EMBEDDING_MODEL


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class KnowledgeStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise KnowledgeError(f"不支持的知识库版本: {version}")
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('enabled', '1')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('index_version', '0')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES('embedding_model', ?)",
                (DEFAULT_EMBEDDING_MODEL,),
            )

    # settings ---------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    @property
    def enabled(self) -> bool:
        return self.get_setting("enabled", "1") == "1"

    def set_enabled(self, enabled: bool) -> None:
        self.set_setting("enabled", "1" if enabled else "0")

    def index_version(self) -> int:
        return int(self.get_setting("index_version", "0") or 0)

    def bump_index_version(self) -> int:
        version = self.index_version() + 1
        self.set_setting("index_version", version)
        return version

    def last_index_at(self) -> str:
        return self.get_setting("last_index_at", "")

    def mark_indexed_at(self) -> None:
        self.set_setting("last_index_at", _now())

    def embedding_dim(self) -> int:
        return int(self.get_setting("embedding_dim", "0") or 0)

    def set_embedding_dim(self, dim: int) -> None:
        if dim > 0:
            self.set_setting("embedding_dim", dim)

    # sources ----------------------------------------------------------

    def list_sources(self) -> list[SourceRow]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, path, enabled, added_at, last_scan_at "
                "FROM sources ORDER BY id"
            ).fetchall()
        return [
            SourceRow(
                id=row["id"],
                path=row["path"],
                enabled=bool(row["enabled"]),
                added_at=row["added_at"],
                last_scan_at=row["last_scan_at"] or "",
            )
            for row in rows
        ]

    def get_source(self, source_id: int) -> SourceRow | None:
        for source in self.list_sources():
            if source.id == source_id:
                return source
        return None

    def add_source(self, raw_path: str) -> SourceRow:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise KnowledgeError("请选择绝对路径的文件夹")
        path = path.resolve()
        if not path.exists() or not path.is_dir():
            raise KnowledgeError("文件夹不存在")
        for existing in self.list_sources():
            other = Path(existing.path)
            if (
                path == other
                or path.is_relative_to(other)
                or other.is_relative_to(path)
            ):
                raise KnowledgeError("该文件夹与已有来源重叠，请选择其他文件夹")
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO sources(path, enabled, added_at) VALUES(?, 1, ?)",
                    (str(path), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise KnowledgeError("该文件夹已在知识库中") from exc
            source_id = int(cursor.lastrowid)
        self.bump_index_version()
        source = self.get_source(source_id)
        if source is None:
            raise KnowledgeError("添加文件夹失败")
        return source

    def remove_source(self, source_id: int) -> None:
        if self.get_source(source_id) is None:
            raise KnowledgeError("来源不存在")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM chunks_fts WHERE document_id IN "
                "(SELECT id FROM documents WHERE source_id = ?)",
                (source_id,),
            )
            connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.bump_index_version()

    def touch_source(self, source_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sources SET last_scan_at = ? WHERE id = ?",
                (_now(), source_id),
            )

    def documents_summary_by_source(self) -> dict[int, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source_id, count(*) AS count FROM documents GROUP BY source_id"
            ).fetchall()
        return {row["source_id"]: int(row["count"]) for row in rows}

    # documents --------------------------------------------------------

    def get_document(self, source_id: int, path: str) -> DocumentRow | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE source_id = ? AND path = ?",
                (source_id, path),
            ).fetchone()
        return self._document(row) if row else None

    def list_documents(self, source_id: int | None = None) -> list[DocumentRow]:
        with self._connect() as connection:
            if source_id is None:
                rows = connection.execute("SELECT * FROM documents ORDER BY id").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM documents WHERE source_id = ? ORDER BY id",
                    (source_id,),
                ).fetchall()
        return [self._document(row) for row in rows]

    @staticmethod
    def _document(row: sqlite3.Row) -> DocumentRow:
        return DocumentRow(
            id=row["id"],
            source_id=row["source_id"],
            path=row["path"],
            rel_path=row["rel_path"],
            size=int(row["size"]),
            mtime=float(row["mtime"]),
            content_hash=row["content_hash"],
            title=row["title"] or "",
            status=row["status"],
            error=row["error"] or "",
            indexed_at=row["indexed_at"] or "",
        )

    def count_documents(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT count(*) FROM documents").fetchone()[0])

    def count_chunks(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def count_document_chunks(self, document_id: int) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM chunks WHERE document_id = ?", (document_id,)
                ).fetchone()[0]
            )

    def touch_document(self, document_id: int, size: int, mtime: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET size = ?, mtime = ? WHERE id = ?",
                (size, mtime, document_id),
            )

    def replace_document(
        self,
        source_id: int,
        path: str,
        rel_path: str,
        size: int,
        mtime: float,
        content_hash: str,
        title: str,
        chunks: list[ChunkInsert],
        status: str = "indexed",
    ) -> int:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO documents("
                "source_id, path, rel_path, size, mtime, content_hash, title, "
                "status, error, indexed_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, '', ?) "
                "ON CONFLICT(source_id, path) DO UPDATE SET "
                "rel_path=excluded.rel_path, size=excluded.size, "
                "mtime=excluded.mtime, content_hash=excluded.content_hash, "
                "title=excluded.title, status=excluded.status, error='', "
                "indexed_at=excluded.indexed_at",
                (
                    source_id,
                    path,
                    rel_path,
                    size,
                    mtime,
                    content_hash,
                    title,
                    status,
                    _now(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM documents WHERE source_id = ? AND path = ?",
                (source_id, path),
            ).fetchone()
            document_id = int(row["id"])
            connection.execute(
                "DELETE FROM chunks_fts WHERE document_id = ?", (document_id,)
            )
            connection.execute(
                "DELETE FROM chunks WHERE document_id = ?", (document_id,)
            )
            for seq, chunk in enumerate(chunks):
                cursor = connection.execute(
                    "INSERT INTO chunks("
                    "document_id, seq, text, heading, page, sheet, slide, "
                    "tokens, embedding, dim, embedding_model) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        seq,
                        chunk.text,
                        chunk.heading,
                        chunk.page,
                        chunk.sheet,
                        chunk.slide,
                        chunk.tokens,
                        chunk.embedding,
                        chunk.dim,
                        chunk.embedding_model,
                    ),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_id, document_id, tokens_text) "
                    "VALUES(?, ?, ?)",
                    (int(cursor.lastrowid), document_id, chunk.tokens_text),
                )
        return document_id

    def mark_document_error(
        self,
        source_id: int,
        path: str,
        rel_path: str,
        size: int,
        mtime: float,
        content_hash: str,
        title: str,
        error: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO documents("
                "source_id, path, rel_path, size, mtime, content_hash, title, "
                "status, error, indexed_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 'error', ?, ?) "
                "ON CONFLICT(source_id, path) DO UPDATE SET "
                "rel_path=excluded.rel_path, size=excluded.size, "
                "mtime=excluded.mtime, content_hash=excluded.content_hash, "
                "title=excluded.title, status='error', error=excluded.error, "
                "indexed_at=excluded.indexed_at",
                (
                    source_id,
                    path,
                    rel_path,
                    size,
                    mtime,
                    content_hash,
                    title,
                    error[:500],
                    _now(),
                ),
            )

    def delete_missing_documents(self, source_id: int, seen_paths: set[str]) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, path FROM documents WHERE source_id = ?", (source_id,)
            ).fetchall()
            for row in rows:
                if row["path"] in seen_paths:
                    continue
                connection.execute(
                    "DELETE FROM chunks_fts WHERE document_id = ?", (row["id"],)
                )
                connection.execute(
                    "DELETE FROM documents WHERE id = ?", (row["id"],)
                )

    def clear_documents(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM documents")
        self.bump_index_version()

    # search support ---------------------------------------------------

    def all_vectors(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT c.id, c.embedding, c.dim "
                "FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "JOIN sources s ON s.id = d.source_id "
                "WHERE d.status = 'indexed' AND s.enabled = 1"
            ).fetchall()

    def chunks_by_ids(self, chunk_ids: list[int]) -> list[sqlite3.Row]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._connect() as connection:
            return connection.execute(
                "SELECT c.id, c.text, c.heading, c.page, c.sheet, c.slide, "
                "d.rel_path AS rel_path, d.path AS absolute_path "
                "FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "JOIN sources s ON s.id = d.source_id "
                f"WHERE c.id IN ({placeholders}) "
                "AND d.status = 'indexed' AND s.enabled = 1",
                chunk_ids,
            ).fetchall()

    def fts_search(self, expression: str, limit: int) -> list[int]:
        if not expression.strip():
            return []
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT chunk_id FROM chunks_fts "
                    "WHERE chunks_fts MATCH ? "
                    "ORDER BY bm25(chunks_fts) LIMIT ?",
                    (expression, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [int(row["chunk_id"]) for row in rows]

    def like_search(self, needle: str, limit: int) -> list[int]:
        pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT c.id FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "JOIN sources s ON s.id = d.source_id "
                "WHERE d.status = 'indexed' AND s.enabled = 1 "
                "AND c.text LIKE ? ESCAPE '\\' LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return [int(row["id"]) for row in rows]
