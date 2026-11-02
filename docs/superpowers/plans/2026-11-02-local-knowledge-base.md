# Local Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, fully local folder knowledge base: background indexing, hybrid retrieval, an agent tool, and a settings panel.

**Architecture:** `knowledge_store.py` owns `knowledge.db` (SQLite + FTS5 + vector blobs). `knowledge_extract.py` discovers and chunks supported files. `knowledge_embed.py` talks to local Ollama. `knowledge_search.py` fuses vector and lexical results with RRF. `knowledge_indexer.py` runs a cancellable background scan, and `knowledge_service.py` bundles the components for the desktop API. `knowledge_tools.py` exposes `search_knowledge_base` to the agent.

**Tech Stack:** Python 3.12 (`D:/Anaconda3/envs/rag/python.exe`), SQLite FTS5, numpy, jieba, Ollama `qwen3-embedding:0.6b`, FastAPI, React 18, TypeScript, Electron, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-local-knowledge-base-design.md`

**Scope:** Development tree only. Do not package or install the desktop application, and do not touch the installed copy. The knowledge database is local.

---

### Task 1: Knowledge store

**Files:**
- Create: `Langgraph/knowledge_store.py`
- Test: `Langgraph/tests/test_knowledge_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_store.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from knowledge_store import (
    ChunkInsert,
    KnowledgeError,
    KnowledgeStore,
)


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_store_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = KnowledgeStore(self.root / "knowledge.db")
        self.source_dir = self.root / "资料"
        self.source_dir.mkdir()
        self.file = self.source_dir / "方案.md"
        self.file.write_text("蓝鲸计划是今年重点项目。", encoding="utf-8")
        self.source = self.store.add_source(str(self.source_dir))

    def _chunk(self, text: str = "蓝鲸计划是今年重点项目。") -> ChunkInsert:
        return ChunkInsert(
            text=text,
            tokens_text="蓝鲸 计划 今年 重点 项目",
            heading="概述",
            page=None,
            sheet="",
            slide=None,
            tokens=12,
            embedding=b"\x00\x00\x80\x3f",
            dim=1,
            embedding_model="fake",
        )

    def test_source_is_added_and_listed(self):
        sources = self.store.list_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].path, str(self.source_dir.resolve()))

    def test_relative_and_missing_sources_are_rejected(self):
        with self.assertRaises(KnowledgeError):
            self.store.add_source("relative/path")
        with self.assertRaises(KnowledgeError):
            self.store.add_source(str(self.root / "missing"))

    def test_overlapping_sources_are_rejected(self):
        nested = self.source_dir / "子目录"
        nested.mkdir()
        with self.assertRaises(KnowledgeError):
            self.store.add_source(str(nested))

    def test_replace_document_is_transactional_and_replaces_old_chunks(self):
        doc_id = self.store.replace_document(
            self.source.id,
            str(self.file.resolve()),
            "方案.md",
            12,
            1.0,
            "hash-1",
            "方案",
            [self._chunk()],
        )
        self.assertEqual(self.store.count_chunks(), 1)

        self.store.replace_document(
            self.source.id,
            str(self.file.resolve()),
            "方案.md",
            12,
            2.0,
            "hash-2",
            "方案",
            [self._chunk("新内容"), self._chunk("第二段")],
        )

        self.assertEqual(self.store.count_chunks(), 2)
        self.assertEqual(self.store.get_document(self.source.id, str(self.file.resolve())).content_hash, "hash-2")

    def test_fts_search_finds_chinese_tokens(self):
        self.store.replace_document(
            self.source.id,
            str(self.file.resolve()),
            "方案.md",
            12,
            1.0,
            "hash-1",
            "方案",
            [self._chunk()],
        )

        ids = self.store.fts_search('"蓝鲸" OR "计划"', 10)

        self.assertEqual(len(ids), 1)

    def test_deleting_a_source_removes_documents_and_fts(self):
        self.store.replace_document(
            self.source.id,
            str(self.file.resolve()),
            "方案.md",
            12,
            1.0,
            "hash-1",
            "方案",
            [self._chunk()],
        )
        self.store.remove_source(self.source.id)

        self.assertEqual(self.store.list_sources(), [])
        self.assertEqual(self.store.count_documents(), 0)
        self.assertEqual(self.store.count_chunks(), 0)
        self.assertEqual(self.store.fts_search('"蓝鲸"', 10), [])

    def test_delete_missing_documents(self):
        self.store.replace_document(
            self.source.id,
            str(self.file.resolve()),
            "方案.md",
            12,
            1.0,
            "hash-1",
            "方案",
            [self._chunk()],
        )

        self.store.delete_missing_documents(self.source.id, set())

        self.assertEqual(self.store.count_documents(), 0)

    def test_index_version_increases_on_changes(self):
        before = self.store.index_version()
        self.store.bump_index_version()
        self.assertGreater(self.store.index_version(), before)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_store
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_store'`.

- [ ] **Step 3: Implement `knowledge_store.py`**

```python
"""SQLite persistence for the local knowledge base."""

from __future__ import annotations

import sqlite3
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
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
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_store
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: add the knowledge base SQLite store"
```

---

### Task 2: Discovery, extraction, and chunking

**Files:**
- Create: `Langgraph/knowledge_extract.py`
- Test: `Langgraph/tests/test_knowledge_extract.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_extract.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from knowledge_extract import (
    ChunkDraft,
    TextUnit,
    chunk_units,
    discover_files,
    extract_units,
)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_discover_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_supported_files_are_found_and_noise_is_skipped(self):
        (self.root / "笔记.md").write_text("内容", encoding="utf-8")
        (self.root / "data.bin").write_text("内容", encoding="utf-8")
        (self.root / ".hidden.md").write_text("内容", encoding="utf-8")
        modules = self.root / "node_modules"
        modules.mkdir()
        (modules / "skip.md").write_text("内容", encoding="utf-8")

        names = {path.name for path in discover_files(self.root)}

        self.assertEqual(names, {"笔记.md"})

    def test_oversized_files_are_skipped(self):
        big = self.root / "big.txt"
        big.write_text("x" * 100, encoding="utf-8")
        with patch("knowledge_extract.MAX_FILE_BYTES", 10):
            names = {path.name for path in discover_files(self.root)}
        self.assertEqual(names, set())


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_extract_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_markdown_produces_heading_units(self):
        path = self.root / "a.md"
        path.write_text("# 标题一\n正文一\n\n## 标题二\n正文二", encoding="utf-8")

        units = extract_units(path)

        self.assertEqual(len(units), 2)
        self.assertIn("标题一", units[0].heading)

    def test_docx_and_xlsx_and_pptx_are_extracted(self):
        docx_path = self.root / "a.docx"
        document = Document()
        document.add_paragraph("段落内容")
        document.save(docx_path)
        self.assertTrue(any("段落内容" in unit.text for unit in extract_units(docx_path)))

        xlsx_path = self.root / "a.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "数据"
        sheet.append(["姓名", "部门"])
        workbook.save(xlsx_path)
        units = extract_units(xlsx_path)
        self.assertTrue(any(unit.sheet == "数据" for unit in units))

        pptx_path = self.root / "a.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = "幻灯片标题"
        presentation.save(pptx_path)
        units = extract_units(pptx_path)
        self.assertTrue(any("幻灯片标题" in unit.text for unit in units))

    def test_pdf_uses_page_units(self):
        class FakePage:
            def extract_text(self):
                return "PDF 内容" * 10

        class FakeReader:
            def __init__(self, path):
                self.pages = [FakePage()]

        with patch("knowledge_extract.PdfReader", FakeReader):
            units = extract_units(self.root / "a.pdf")

        self.assertEqual(units[0].page, 1)
        self.assertIn("PDF 内容", units[0].text)


class ChunkingTests(unittest.TestCase):
    def test_long_text_is_split_around_the_target_size(self):
        unit = TextUnit(text="段落。" * 500, heading="标题")
        chunks = chunk_units([unit], chunk_size=100, overlap=20, max_chunk=200)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 240 for chunk in chunks))
        self.assertIn("[标题]", "".join(chunk.embed_text for chunk in chunks))

    def test_metadata_is_carried_into_every_chunk(self):
        unit = TextUnit(text="内容" * 100, heading="架构", page=3)
        chunks = chunk_units([unit], chunk_size=100, overlap=20)

        self.assertTrue(all(chunk.heading == "架构" for chunk in chunks))
        self.assertTrue(all(chunk.page == 3 for chunk in chunks))
        self.assertTrue(all("[第3页]" in chunk.embed_text for chunk in chunks))

    def test_short_file_is_kept(self):
        chunks = chunk_units([TextUnit(text="很短")], chunk_size=600)
        self.assertEqual(len(chunks), 1)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_extract
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_extract'`.

- [ ] **Step 3: Implement `knowledge_extract.py`**

```python
"""File discovery, text extraction, and chunking for the knowledge base."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from pptx import Presentation

from context_builder import estimate_text_tokens

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".json",
}
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".cache",
    "target",
}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_XLSX_ROWS = 2000
MAX_XLSX_CHARS = 50_000
MAX_CSV_ROWS = 5000
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100
MIN_CHUNK = 50
MAX_CHUNK = 1000


@dataclass(frozen=True)
class TextUnit:
    text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    embed_text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None
    tokens: int = 0


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(8192)
    except OSError:
        return True


def discover_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and name.lower() not in SKIP_DIRS
            and not (current / name).is_symlink()
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = current / name
            if path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            if path.suffix.lower() in {".txt", ".md", ".markdown", ".csv", ".json"}:
                if _looks_binary(path):
                    continue
            yield path


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return _decode(data)[:MAX_TEXT_CHARS]


def _extract_markdown(path: Path) -> list[TextUnit]:
    units: list[TextUnit] = []
    stack: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            units.append(TextUnit(text=text, heading=" / ".join(stack)))
        buffer.clear()

    for line in _read_text(path).splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            flush()
            level = len(match.group(1))
            stack[:] = stack[: level - 1]
            stack.append(match.group(2).strip())
        else:
            buffer.append(line)
    flush()
    return units


def _extract_docx(path: Path) -> list[TextUnit]:
    document = Document(str(path))
    units: list[TextUnit] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            units.append(TextUnit(text=text, heading=heading))
        buffer.clear()

    for paragraph in document.paragraphs:
        style = paragraph.style.name if paragraph.style else ""
        if style.startswith("Heading") or style.startswith("标题"):
            flush()
            heading = paragraph.text.strip()
            continue
        if paragraph.text.strip():
            buffer.append(paragraph.text)
    flush()

    for table in document.tables:
        rows = []
        for row in table.rows:
            rows.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = "\n".join(rows).strip()
        if text:
            units.append(TextUnit(text=text, heading=heading))
    return units


def _extract_xlsx(path: Path) -> list[TextUnit]:
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    units: list[TextUnit] = []
    try:
        for sheet in workbook.worksheets:
            lines: list[str] = []
            chars = 0
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_XLSX_ROWS or chars >= MAX_XLSX_CHARS:
                    break
                line = " | ".join("" if value is None else str(value) for value in row).strip()
                if line:
                    lines.append(line)
                    chars += len(line)
            text = "\n".join(lines).strip()
            if text:
                units.append(TextUnit(text=text, sheet=sheet.title))
    finally:
        workbook.close()
    return units


def _extract_pptx(path: Path) -> list[TextUnit]:
    presentation = Presentation(str(path))
    units: list[TextUnit] = []
    for index, slide in enumerate(presentation.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                texts.append(shape.text.strip())
        text = "\n".join(texts).strip()
        if text:
            units.append(TextUnit(text=text, slide=index))
    return units


def _extract_csv(path: Path) -> list[TextUnit]:
    text = _read_text(path)
    lines: list[str] = []
    for index, row in enumerate(csv.reader(text.splitlines())):
        if index >= MAX_CSV_ROWS:
            break
        lines.append(" | ".join(row))
    joined = "\n".join(lines).strip()
    return [TextUnit(text=joined)] if joined else []


def _extract_json(path: Path) -> list[TextUnit]:
    text = _read_text(path)
    try:
        data = json.loads(text)
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pass
    text = text[:MAX_TEXT_CHARS].strip()
    return [TextUnit(text=text)] if text else []


def _extract_pdf(path: Path) -> list[TextUnit]:
    reader = PdfReader(str(path))
    units: list[TextUnit] = []
    total = 0
    for index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        total += len(text)
        if text:
            units.append(TextUnit(text=text, page=index))
    if total < 20:
        return []
    return units


def extract_units(path: Path) -> list[TextUnit]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return _extract_markdown(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".xlsx":
        return _extract_xlsx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".csv":
        return _extract_csv(path)
    if suffix == ".json":
        return _extract_json(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    text = _read_text(path).strip()
    return [TextUnit(text=text)] if text else []


def _split_long(paragraph: str, limit: int) -> list[str]:
    if len(paragraph) <= limit:
        return [paragraph]
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        window = remaining[: limit + 50]
        cut = max(
            window.rfind("。"),
            window.rfind("！"),
            window.rfind("？"),
            window.rfind("；"),
            window.rfind(". "),
            window.rfind("\n"),
        )
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def _embed_text(unit: TextUnit, chunk: str) -> str:
    parts: list[str] = []
    if unit.heading:
        parts.append(f"[标题] {unit.heading}")
    if unit.page is not None:
        parts.append(f"[第{unit.page}页]")
    if unit.sheet:
        parts.append(f"[工作表] {unit.sheet}")
    if unit.slide is not None:
        parts.append(f"[第{unit.slide}页幻灯片]")
    parts.append(chunk)
    return "\n".join(parts)


def chunk_units(
    units: list[TextUnit],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    min_chunk: int = MIN_CHUNK,
    max_chunk: int = MAX_CHUNK,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for unit in units:
        text = unit.text.strip()
        if not text:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        pieces = [
            piece
            for paragraph in paragraphs
            for piece in _split_long(paragraph, max_chunk)
        ]
        buffer = ""
        unit_chunks: list[str] = []
        for piece in pieces:
            if not buffer:
                buffer = piece
            elif len(buffer) + 2 + len(piece) <= chunk_size:
                buffer = f"{buffer}\n\n{piece}"
            else:
                unit_chunks.append(buffer)
                tail = buffer[-overlap:] if overlap > 0 else ""
                buffer = (tail + "\n" + piece).strip()
        if buffer:
            unit_chunks.append(buffer)
        for index, chunk in enumerate(unit_chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(chunk) < min_chunk and len(unit_chunks) > 1:
                continue
            embed_text = _embed_text(unit, chunk)
            drafts.append(
                ChunkDraft(
                    text=chunk[:max_chunk],
                    embed_text=embed_text,
                    heading=unit.heading,
                    page=unit.page,
                    sheet=unit.sheet,
                    slide=unit.slide,
                    tokens=estimate_text_tokens(embed_text),
                )
            )
    return drafts
```

- [ ] **Step 4: Run the tests and fix the expected details**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_extract
```

Expected: PASS. If the Markdown heading test yields three units because the text before the first heading is empty, adjust the fixture; the extractor flushes only non-empty sections.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_extract.py tests/test_knowledge_extract.py
git commit -m "feat: add knowledge file discovery and chunking"
```

---

### Task 3: Ollama embedding client

**Files:**
- Create: `Langgraph/knowledge_embed.py`
- Test: `Langgraph/tests/test_knowledge_embed.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_embed.py`:

```python
from __future__ import annotations

import json
import math
import unittest

import httpx

from knowledge_embed import EmbeddingStatus, OllamaEmbedder


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3-embedding:0.6b"}]},
        )
    if request.url.path == "/api/embed":
        body = json.loads(request.content)
        vectors = [[3.0, 4.0] for _ in body["input"]]
        return httpx.Response(200, json={"embeddings": vectors})
    return httpx.Response(404)


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.embedder = OllamaEmbedder(
            base_url="http://127.0.0.1:11434",
            model="qwen3-embedding:0.6b",
            transport=httpx.MockTransport(_handler),
        )

    def test_status_reports_the_model(self):
        status = self.embedder.status()

        self.assertIsInstance(status, EmbeddingStatus)
        self.assertTrue(status.available)
        self.assertEqual(status.model, "qwen3-embedding:0.6b")

    def test_embedding_is_normalized(self):
        vector = self.embedder.embed(["hello"])[0]

        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0)
        self.assertAlmostEqual(vector[0], 0.6)

    def test_base_url_with_v1_is_accepted(self):
        embedder = OllamaEmbedder(
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3-embedding:0.6b",
            transport=httpx.MockTransport(_handler),
        )
        self.assertTrue(embedder.status().available)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_embed
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `knowledge_embed.py`**

```python
"""Local Ollama embedding client for the knowledge base."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class EmbeddingError(RuntimeError):
    """Embedding request failed."""


@dataclass(frozen=True)
class EmbeddingStatus:
    model: str
    available: bool
    reason: str
    warning: str
    dim: int = 0


def _root_url(base_url: str) -> str:
    base = str(base_url or DEFAULT_OLLAMA_URL).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _loopback_warning(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return ""
    return "嵌入服务不在本机，资料会发送到该服务"


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        batch_size: int = 16,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = _root_url(base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL))
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL", DEFAULT_EMBEDDING_MODEL)
        self.batch_size = max(1, int(batch_size))
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def status(self) -> EmbeddingStatus:
        warning = _loopback_warning(self.base_url)
        try:
            response = self._client.get(f"{self.base_url}/api/tags", timeout=3.0)
            response.raise_for_status()
            names = [
                str(item.get("name") or "")
                for item in response.json().get("models", [])
            ]
            available = self.model in names or f"{self.model}:latest" in names
            reason = "" if available else f"Ollama 中未安装 {self.model}"
            return EmbeddingStatus(
                model=self.model,
                available=available,
                reason=reason,
                warning=warning,
            )
        except Exception as exc:  # noqa: BLE001
            return EmbeddingStatus(
                model=self.model,
                available=False,
                reason=f"无法连接 Ollama: {exc}",
                warning=warning,
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(2):
            try:
                response = self._client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch},
                )
                response.raise_for_status()
                data = response.json().get("embeddings")
                if not isinstance(data, list) or len(data) != len(batch):
                    raise EmbeddingError("嵌入服务返回的数量不匹配")
                return [self._normalize(vector) for vector in data]
            except (httpx.HTTPError, ValueError, EmbeddingError) as exc:
                last = exc
                if attempt == 0:
                    time.sleep(1.0)
        raise EmbeddingError(f"嵌入失败: {last}") from last

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm == 0:
            return [0.0 for _ in vector]
        return [float(value) / norm for value in vector]
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_embed
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_embed.py tests/test_knowledge_embed.py
git commit -m "feat: add local Ollama embedding client"
```

---

### Task 4: Hybrid search

**Files:**
- Create: `Langgraph/knowledge_search.py`
- Test: `Langgraph/tests/test_knowledge_search.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_search.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from knowledge_embed import EmbeddingStatus
from knowledge_search import KnowledgeSearcher
from knowledge_store import ChunkInsert, KnowledgeStore


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, vector: list[float] | None = None, error: Exception | None = None):
        self.vector = vector or [1.0, 0.0]
        self.error = error

    def status(self):
        return EmbeddingStatus(
            model=self.model,
            available=self.error is None,
            reason="" if self.error is None else str(self.error),
            warning="",
        )

    def embed(self, texts):
        if self.error:
            raise self.error
        return [list(self.vector) for _ in texts]


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_search_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = KnowledgeStore(self.root / "knowledge.db")
        self.source_dir = self.root / "资料"
        self.source_dir.mkdir()
        self.source = self.store.add_source(str(self.source_dir))

    def _add(self, text: str, vector: list[float], tokens_text: str, name: str = "a.md"):
        path = self.source_dir / name
        path.write_text(text, encoding="utf-8")
        self.store.replace_document(
            self.source.id,
            str(path.resolve()),
            name,
            len(text),
            1.0,
            "hash-" + name,
            name,
            [
                ChunkInsert(
                    text=text,
                    tokens_text=tokens_text,
                    heading="概述",
                    tokens=10,
                    embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                    dim=len(vector),
                    embedding_model="fake-embed",
                )
            ],
        )

    def test_vector_search_finds_the_closest_chunk(self):
        self._add("蓝鲸计划", [1.0, 0.0], "蓝鲸 计划", "a.md")
        self._add("完全无关", [0.0, 1.0], "完全 无关", "b.md")
        searcher = KnowledgeSearcher(self.store, FakeEmbedder([1.0, 0.0]))

        result = searcher.search("蓝鲸", top_k=1)

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["path"], "a.md")

    def test_lexical_search_works_when_embedding_fails(self):
        self._add("蓝鲸计划", [1.0, 0.0], "蓝鲸 计划", "a.md")
        searcher = KnowledgeSearcher(
            self.store, FakeEmbedder(error=RuntimeError("Ollama down"))
        )

        result = searcher.search("蓝鲸", top_k=1)

        self.assertFalse(result["embedding_used"])
        self.assertTrue(result["warning"])
        self.assertEqual(result["results"][0]["path"], "a.md")

    def test_disabled_returns_a_reason(self):
        self.store.set_enabled(False)
        searcher = KnowledgeSearcher(self.store, FakeEmbedder())

        result = searcher.search("任意", top_k=3)

        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(result["results"], [])
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_search
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `knowledge_search.py`**

```python
"""Hybrid vector and lexical search for the knowledge base."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import jieba
import numpy as np

from knowledge_embed import EmbeddingError
from knowledge_store import KnowledgeStore

RRF_K = 60
VECTOR_LIMIT = 20
LEXICAL_LIMIT = 20
MAX_RESULT_CHARS = 1200


class KnowledgeSearcher:
    def __init__(self, store: KnowledgeStore, embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._cache_version: int | None = None
        self._ids: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._dim: int | None = None

    def _vector_cache(self) -> tuple[np.ndarray, np.ndarray]:
        version = self.store.index_version()
        if (
            self._cache_version == version
            and self._ids is not None
            and self._matrix is not None
        ):
            return self._ids, self._matrix
        ids: list[int] = []
        vectors: list[np.ndarray] = []
        dim: int | None = None
        for row in self.store.all_vectors():
            vector = np.frombuffer(row["embedding"], dtype=np.float32)
            if vector.size == 0:
                continue
            if dim is None:
                dim = int(vector.size)
            if vector.size != dim:
                continue
            ids.append(int(row["id"]))
            vectors.append(vector)
        if vectors:
            matrix = np.vstack(vectors).astype(np.float32, copy=False)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            matrix = matrix / norms
            id_array = np.asarray(ids, dtype=np.int64)
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)
            id_array = np.zeros((0,), dtype=np.int64)
        self._cache_version = version
        self._ids = id_array
        self._matrix = matrix
        self._dim = dim
        return id_array, matrix

    def _lexical(self, query: str) -> list[int]:
        tokens = [token.strip() for token in jieba.cut_for_search(query) if token.strip()]
        if not tokens:
            return self.store.like_search(query, LEXICAL_LIMIT)
        expression = " OR ".join(
            '"' + token.replace('"', '""') + '"' for token in tokens
        )
        return self.store.fts_search(expression, LEXICAL_LIMIT)

    def search(self, query: str, top_k: int = 5) -> dict[str, Any]:
        top_k = max(1, min(int(top_k), 10))
        if not self.store.enabled:
            return self._empty(query, reason="disabled", enabled=False)
        if self.store.count_chunks() == 0:
            return self._empty(query, reason="empty", enabled=True)

        warning = ""
        query_vector: list[float] | None = None
        try:
            query_vector = self.embedder.embed([query])[0]
        except Exception as exc:  # noqa: BLE001
            warning = f"嵌入模型不可用，已仅用关键词检索（{exc}）"

        vector_ids: list[int] = []
        if query_vector is not None:
            ids, matrix = self._vector_cache()
            if ids.size:
                vector = np.asarray(query_vector, dtype=np.float32)
                if matrix.shape[1] == vector.shape[0]:
                    scores = matrix @ vector
                    count = min(VECTOR_LIMIT, scores.shape[0])
                    top = np.argpartition(-scores, count - 1)[:count]
                    top = top[np.argsort(-scores[top])]
                    vector_ids = [int(ids[index]) for index in top]
                else:
                    warning = "向量维度不一致，已仅用关键词检索"

        lexical_ids = self._lexical(query)
        fused: dict[int, float] = defaultdict(float)
        for rank, chunk_id in enumerate(vector_ids, 1):
            fused[chunk_id] += 1.0 / (RRF_K + rank)
        for rank, chunk_id in enumerate(lexical_ids, 1):
            fused[chunk_id] += 1.0 / (RRF_K + rank)
        ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_k]
        rows = self.store.chunks_by_ids([chunk_id for chunk_id, _ in ranked])
        by_id = {int(row["id"]): row for row in rows}
        results = []
        for chunk_id, score in ranked:
            row = by_id.get(chunk_id)
            if row is None:
                continue
            text = str(row["text"])
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + "…"
            results.append(
                {
                    "chunk_id": chunk_id,
                    "path": row["rel_path"],
                    "absolute_path": row["absolute_path"],
                    "heading": row["heading"] or "",
                    "page": row["page"],
                    "sheet": row["sheet"] or "",
                    "slide": row["slide"],
                    "score": round(float(score), 6),
                    "text": text,
                }
            )
        return {
            "query": query,
            "enabled": True,
            "embedding_used": query_vector is not None,
            "warning": warning,
            "results": results,
            "reason": "" if results else "no_match",
        }

    @staticmethod
    def _empty(query: str, reason: str, enabled: bool) -> dict[str, Any]:
        return {
            "query": query,
            "enabled": enabled,
            "embedding_used": False,
            "warning": "",
            "results": [],
            "reason": reason,
        }
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_search
```

Expected: PASS. If the lexical-only test finds no result, inspect `jieba.cut_for_search("蓝鲸")`; add a space-separated token in the test data if needed.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_search.py tests/test_knowledge_search.py
git commit -m "feat: add hybrid knowledge search with RRF"
```

---

### Task 5: Indexer and service facade

**Files:**
- Create: `Langgraph/knowledge_indexer.py`
- Create: `Langgraph/knowledge_service.py`
- Test: `Langgraph/tests/test_knowledge_indexer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_indexer.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from knowledge_embed import EmbeddingStatus
from knowledge_indexer import KnowledgeIndexer
from knowledge_store import KnowledgeStore


class FakeEmbedder:
    model = "fake-embed"

    def status(self):
        return EmbeddingStatus(model=self.model, available=True, reason="", warning="")

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_index_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = KnowledgeStore(self.root / "knowledge.db")
        self.source_dir = self.root / "资料"
        self.source_dir.mkdir()
        self.source = self.store.add_source(str(self.source_dir))
        self.indexer = KnowledgeIndexer(self.store, FakeEmbedder())

    def _wait(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.indexer.is_running():
                return self.indexer.snapshot()
            time.sleep(0.02)
        raise AssertionError("indexer did not finish")

    def test_indexes_a_markdown_file(self):
        (self.source_dir / "a.md").write_text("# 概述\n蓝鲸计划。", encoding="utf-8")

        self.assertTrue(self.indexer.start())
        state = self._wait()

        self.assertEqual(state["state"], "done")
        self.assertEqual(self.store.count_documents(), 1)
        self.assertGreaterEqual(self.store.count_chunks(), 1)

    def test_unchanged_files_are_skipped(self):
        path = self.source_dir / "a.md"
        path.write_text("# 概述\n蓝鲸计划。", encoding="utf-8")
        self.indexer.start()
        self._wait()
        first = self.store.count_chunks()

        self.indexer.start()
        self._wait()

        self.assertEqual(self.store.count_chunks(), first)

    def test_changed_file_is_reindexed(self):
        path = self.source_dir / "a.md"
        path.write_text("# 概述\n第一版。", encoding="utf-8")
        self.indexer.start()
        self._wait()
        path.write_text("# 概述\n第二版，内容更长。", encoding="utf-8")

        self.indexer.start()
        self._wait()

        row = self.store.list_documents()[0]
        self.assertEqual(row.status, "indexed")
        self.assertNotEqual(row.content_hash, "")

    def test_deleted_file_is_removed(self):
        path = self.source_dir / "a.md"
        path.write_text("# 概述\n内容。", encoding="utf-8")
        self.indexer.start()
        self._wait()
        path.unlink()

        self.indexer.start()
        self._wait()

        self.assertEqual(self.store.count_documents(), 0)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_indexer
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `knowledge_indexer.py`**

```python
"""Background indexing for the local knowledge base."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import jieba
import numpy as np

from knowledge_embed import EmbeddingError
from knowledge_extract import chunk_units, discover_files, extract_units
from knowledge_store import ChunkInsert, KnowledgeError, KnowledgeStore

MAX_CHUNKS = 50_000


class _Cancelled(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class IndexerProgress:
    state: str = "idle"
    total_files: int = 0
    processed_files: int = 0
    indexed_documents: int = 0
    failed_documents: int = 0
    current_file: str = ""
    started_at: str = ""
    finished_at: str = ""
    message: str = ""


class KnowledgeIndexer:
    def __init__(self, store: KnowledgeStore, embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._progress = IndexerProgress()

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self._progress)

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, rebuild: bool = False) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel.clear()
            self._progress = IndexerProgress(
                state="scanning", started_at=_now()
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(rebuild,),
                daemon=True,
                name="knowledge-indexer",
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def _update(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._progress, key, value)

    def _run(self, rebuild: bool) -> None:
        indexed = 0
        failed = 0
        processed = 0
        try:
            status = self.embedder.status()
            if not status.available:
                raise EmbeddingError(status.reason)
            if rebuild:
                self.store.clear_documents()
            sources = self.store.list_sources()
            if not sources:
                self._finish("done", indexed, failed, processed)
                return

            files: list[tuple[int, Path, Path]] = []
            for source in sources:
                root = Path(source.path)
                for path in discover_files(root):
                    files.append((source.id, root, path))
            self._update(total_files=len(files))

            seen: dict[int, set[str]] = {source.id: set() for source in sources}
            for source_id, root, path in files:
                if self._cancel.is_set():
                    raise _Cancelled()
                absolute = str(path.resolve())
                seen[source_id].add(absolute)
                relative = path.relative_to(root).as_posix()
                try:
                    stat = path.stat()
                except OSError:
                    processed += 1
                    continue
                existing = self.store.get_document(source_id, absolute)
                if (
                    existing is not None
                    and existing.status != "error"
                    and existing.size == stat.st_size
                    and abs(existing.mtime - stat.st_mtime) < 1e-6
                ):
                    processed += 1
                    self._update(processed_files=processed)
                    continue
                digest = _file_hash(path)
                if (
                    existing is not None
                    and existing.status == "indexed"
                    and existing.content_hash == digest
                ):
                    self.store.touch_document(existing.id, stat.st_size, stat.st_mtime)
                    processed += 1
                    self._update(processed_files=processed)
                    continue

                self._update(state="extracting", current_file=relative)
                try:
                    drafts = chunk_units(extract_units(path))
                    if not drafts:
                        self.store.replace_document(
                            source_id,
                            absolute,
                            relative,
                            stat.st_size,
                            stat.st_mtime,
                            digest,
                            path.stem,
                            [],
                            status="empty",
                        )
                        processed += 1
                        self._update(processed_files=processed)
                        continue
                    existing_chunks = (
                        self.store.count_document_chunks(existing.id)
                        if existing is not None
                        else 0
                    )
                    if (
                        self.store.count_chunks()
                        - existing_chunks
                        + len(drafts)
                        > MAX_CHUNKS
                    ):
                        raise KnowledgeError("知识库已达 5 万段上限")
                    self._update(state="embedding", current_file=relative)
                    vectors: list[list[float]] = []
                    for start in range(0, len(drafts), 16):
                        if self._cancel.is_set():
                            raise _Cancelled()
                        batch = drafts[start : start + 16]
                        vectors.extend(
                            self.embedder.embed([draft.embed_text for draft in batch])
                        )
                    if len(vectors) != len(drafts):
                        raise EmbeddingError("嵌入数量与分块数量不一致")
                    dim = len(vectors[0])
                    self.store.set_embedding_dim(dim)
                    records = [
                        ChunkInsert(
                            text=draft.text,
                            tokens_text=" ".join(jieba.cut_for_search(draft.text)),
                            heading=draft.heading,
                            page=draft.page,
                            sheet=draft.sheet,
                            slide=draft.slide,
                            tokens=draft.tokens,
                            embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                            dim=dim,
                            embedding_model=self.embedder.model,
                        )
                        for draft, vector in zip(drafts, vectors)
                    ]
                    self.store.replace_document(
                        source_id,
                        absolute,
                        relative,
                        stat.st_size,
                        stat.st_mtime,
                        digest,
                        path.stem,
                        records,
                        status="indexed",
                    )
                    indexed += 1
                except KnowledgeError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.store.mark_document_error(
                        source_id,
                        absolute,
                        relative,
                        stat.st_size,
                        stat.st_mtime,
                        digest,
                        path.stem,
                        str(exc),
                    )
                processed += 1
                self._update(
                    processed_files=processed,
                    indexed_documents=indexed,
                    failed_documents=failed,
                )

            for source in sources:
                self.store.delete_missing_documents(
                    source.id, seen.get(source.id, set())
                )
                self.store.touch_source(source.id)
            self._finish("done", indexed, failed, processed)
        except _Cancelled:
            self._finish("cancelled", indexed, failed, processed)
        except Exception as exc:  # noqa: BLE001
            self.store.bump_index_version()
            self._update(
                state="error",
                message=str(exc)[:500],
                finished_at=_now(),
                indexed_documents=indexed,
                failed_documents=failed,
                processed_files=processed,
            )

    def _finish(self, state: str, indexed: int, failed: int, processed: int) -> None:
        self.store.mark_indexed_at()
        self.store.bump_index_version()
        self._update(
            state=state,
            finished_at=_now(),
            indexed_documents=indexed,
            failed_documents=failed,
            processed_files=processed,
            current_file="",
        )
```

Note: this code references `KnowledgeError` in `_run`; import it from `knowledge_store`.

- [ ] **Step 4: Implement `knowledge_service.py`**

```python
"""Facade that bundles the knowledge store, embedder, searcher, and indexer."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from knowledge_embed import OllamaEmbedder
from knowledge_indexer import KnowledgeIndexer
from knowledge_search import KnowledgeSearcher
from knowledge_store import KnowledgeError, KnowledgeStore


class KnowledgeBusyError(RuntimeError):
    """An indexing run is already active."""


class KnowledgeService:
    def __init__(self, data_dir: Path, embedder=None, autostart: bool = True):
        self.data_dir = Path(data_dir)
        self.store = KnowledgeStore(self.data_dir / "knowledge.db")
        self.embedder = embedder or OllamaEmbedder()
        self.searcher = KnowledgeSearcher(self.store, self.embedder)
        self.indexer = KnowledgeIndexer(self.store, self.embedder)
        self.autostart = autostart

    def startup(self) -> None:
        if not self.autostart:
            return
        timer = threading.Timer(5.0, self._startup_scan)
        timer.daemon = True
        timer.start()

    def _startup_scan(self) -> None:
        if self.store.enabled and self.store.list_sources():
            self.indexer.start(rebuild=False)

    def shutdown(self) -> None:
        self.indexer.cancel()

    def status(self) -> dict[str, Any]:
        embedding = self.embedder.status()
        progress = self.indexer.snapshot()
        counts = self.store.documents_summary_by_source()
        return {
            "enabled": self.store.enabled,
            "indexing": self.indexer.is_running(),
            "state": progress["state"],
            "progress": {
                "total_files": progress["total_files"],
                "processed_files": progress["processed_files"],
                "indexed_documents": progress["indexed_documents"],
                "failed_documents": progress["failed_documents"],
                "current_file": progress["current_file"],
                "started_at": progress["started_at"],
                "finished_at": progress["finished_at"],
                "message": progress["message"],
            },
            "sources": [
                {
                    "id": source.id,
                    "path": source.path,
                    "document_count": counts.get(source.id, 0),
                    "enabled": source.enabled,
                }
                for source in self.store.list_sources()
            ],
            "documents": self.store.count_documents(),
            "chunks": self.store.count_chunks(),
            "last_index_at": self.store.last_index_at(),
            "embedding": {
                "model": self.embedder.model,
                "available": embedding.available,
                "reason": embedding.reason,
                "warning": embedding.warning,
                "dim": self.store.embedding_dim(),
            },
        }

    def add_source(self, path: str) -> dict[str, Any]:
        self.store.add_source(path)
        self.indexer.start(rebuild=False)
        return self.status()

    def remove_source(self, source_id: int) -> dict[str, Any]:
        self.store.remove_source(source_id)
        return self.status()

    def start_index(self, rebuild: bool = False) -> dict[str, Any]:
        if not self.indexer.start(rebuild=rebuild):
            raise KnowledgeBusyError("已有索引任务在运行")
        return self.status()

    def cancel(self) -> dict[str, Any]:
        self.indexer.cancel()
        return self.status()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.store.set_enabled(enabled)
        if not enabled:
            self.indexer.cancel()
        return self.status()

    def search(self, query: str, top_k: int = 5) -> dict[str, Any]:
        return self.searcher.search(query, top_k=top_k)

    def tools(self) -> list:
        from knowledge_tools import build_knowledge_tools

        return build_knowledge_tools(self.searcher)
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_indexer
```

Expected: PASS. If `test_changed_file_is_reindexed` does not observe a new hash, ensure the test sleeps or changes the file size; mtime resolution on Windows can be coarse, but the content hash path also triggers.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_indexer.py knowledge_service.py tests/test_knowledge_indexer.py
git commit -m "feat: index knowledge folders in the background"
```

---

### Task 6: Agent tool and core tool wiring

**Files:**
- Create: `Langgraph/knowledge_tools.py`
- Modify: `Langgraph/graph_agent.py` (CORE_TOOL_NAMES, collect_tools, SYSTEM_PROMPT)
- Test: `Langgraph/tests/test_knowledge_tools.py`, `Langgraph/tests/test_core_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_tools.py`:

```python
from __future__ import annotations

import unittest

from knowledge_tools import build_knowledge_tools


class FakeSearcher:
    def __init__(self, result):
        self.result = result

    def search(self, query, top_k=5):
        return self.result


class KnowledgeToolTests(unittest.TestCase):
    def _tool(self, result):
        return build_knowledge_tools(FakeSearcher(result))[0]

    def test_formats_results_with_sources(self):
        tool = self._tool(
            {
                "enabled": True,
                "embedding_used": True,
                "warning": "",
                "results": [
                    {
                        "path": "项目A/方案.md",
                        "absolute_path": "D:/资料/项目A/方案.md",
                        "heading": "架构",
                        "page": None,
                        "sheet": "",
                        "slide": None,
                        "score": 0.031,
                        "text": "蓝鲸计划。",
                    }
                ],
                "reason": "",
            }
        )

        output = tool.invoke({"query": "蓝鲸", "top_k": 1})

        self.assertIn("项目A/方案.md", output)
        self.assertIn("D:/资料/项目A/方案.md", output)
        self.assertIn("蓝鲸计划。", output)

    def test_disabled_message(self):
        tool = self._tool(
            {"enabled": False, "embedding_used": False, "warning": "", "results": [], "reason": "disabled"}
        )
        self.assertIn("未启用", tool.invoke({"query": "x"}))

    def test_empty_message(self):
        tool = self._tool(
            {"enabled": True, "embedding_used": True, "warning": "", "results": [], "reason": "empty"}
        )
        self.assertIn("还没有索引内容", tool.invoke({"query": "x"}))
```

Add to `tests/test_core_tools.py`:

```python
from langchain_core.tools import StructuredTool


class CoreExtraToolTests(unittest.TestCase):
    def test_local_keeps_core_extra_tools_only(self):
        def echo():
            return "ok"

        kb = StructuredTool.from_function(
            func=echo,
            name="search_knowledge_base",
            description="知识库",
        )
        mcp = StructuredTool.from_function(
            func=echo,
            name="mcp__demo__echo",
            description="MCP",
        )

        tools = collect_tools(
            model_key="local", extra_tools=[kb, mcp]
        )
        names = {tool.name for tool in tools}

        self.assertIn("search_knowledge_base", names)
        self.assertNotIn("mcp__demo__echo", names)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_tools tests.test_core_tools
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_tools'` and the core-extra assertion.

- [ ] **Step 3: Implement `knowledge_tools.py`**

```python
"""Agent tool for searching the local knowledge base."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="要在本地知识库中检索的问题或关键词")
    top_k: int = Field(default=5, ge=1, le=10, description="返回的结果数量")


def build_knowledge_tools(searcher) -> list:
    def search_knowledge_base(query: str, top_k: int = 5) -> str:
        result = searcher.search(query, top_k=top_k)
        if not result.get("enabled"):
            return "[知识库结果] 知识库未启用。请在设置 → 知识库中打开。"
        if result.get("reason") == "empty":
            return (
                "[知识库结果] 知识库还没有索引内容。"
                "请先在设置 → 知识库中添加文件夹。"
            )
        if not result.get("results"):
            return (
                "[知识库结果] 未找到相关内容。"
                "可以在设置 → 知识库中添加文件夹或更新索引。"
            )
        lines = ["[知识库结果]"]
        if result.get("warning"):
            lines.append(f"[提示] {result['warning']}")
        for index, item in enumerate(result["results"], 1):
            location: list[str] = []
            if item.get("heading"):
                location.append(f"标题: {item['heading']}")
            if item.get("page") is not None:
                location.append(f"第{item['page']}页")
            if item.get("sheet"):
                location.append(f"工作表: {item['sheet']}")
            if item.get("slide") is not None:
                location.append(f"第{item['slide']}页幻灯片")
            suffix = " · " + " · ".join(location) if location else ""
            lines.append(
                f"{index}. 来源: {item['path']}（{item['absolute_path']}）"
                f"{suffix} · 相关度 {float(item['score']):.3f}"
            )
            lines.append(f"   {item['text']}")
        return "\n".join(lines)

    return [
        StructuredTool.from_function(
            func=search_knowledge_base,
            name="search_knowledge_base",
            description=(
                "在本地知识库中检索用户添加的文件夹。"
                "当用户询问自己的资料、文档、笔记、之前说过的内容，"
                "或提到某个文件/主题可能收在知识库时，优先使用此工具。"
                "普通常识问题不要调用。"
            ),
            args_schema=KnowledgeSearchInput,
        )
    ]
```

- [ ] **Step 4: Update `graph_agent.py`**

Add `"search_knowledge_base"` to `CORE_TOOL_NAMES`.

Because the core set now names a tool that lives in `extra_tools` rather than
`TOOLS`, update the existing assertion in `tests/test_core_tools.py`:

```python
    def test_local_model_gets_only_core_tools(self):
        tools = collect_tools(model_key="local")
        names = {tool.name for tool in tools}

        self.assertEqual(
            names, set(CORE_TOOL_NAMES) - {"search_knowledge_base"}
        )
        self.assertIn("read_file", names)
        self.assertNotIn("read_docx", names)
```

In `collect_tools`, replace the `if not core_only:` block with:

```python
    if core_only:
        if extra_tools:
            tools = tools + [
                tool
                for tool in extra_tools
                if getattr(tool, "name", "") in CORE_TOOL_NAMES
            ]
    else:
        if include_subagent_tools and manager is not None:
            from subagent_tools import build_subagent_tools

            tools = tools + build_subagent_tools(manager)
        if extra_tools:
            tools = tools + list(extra_tools)
```

Append to `SYSTEM_PROMPT`:

```python
SYSTEM_PROMPT += (
    "\n\n知识库：当用户询问自己的资料、文档、笔记、之前说过的内容，"
    "或明确提到某个文件/主题可能收在本地知识库时，先用 search_knowledge_base 检索；"
    "普通常识问题不要调用。检索结果不足时如实说明，可以建议用户"
    "在设置 → 知识库中添加文件夹。使用知识库内容回答时，"
    "在相应句子后标注“来源: 路径”。"
)
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_tools tests.test_core_tools
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add knowledge_tools.py graph_agent.py tests/test_knowledge_tools.py tests/test_core_tools.py
git commit -m "feat: expose search_knowledge_base to the agent"
```

---

### Task 7: Desktop API endpoints and runtime wiring

**Files:**
- Modify: `Langgraph/desktop_api.py`
- Test: `Langgraph/tests/test_knowledge_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_api.py`:

```python
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from desktop_api import create_app
from knowledge_embed import EmbeddingStatus
from knowledge_service import KnowledgeService


class FakeEmbedder:
    model = "fake-embed"

    def status(self):
        return EmbeddingStatus(model=self.model, available=True, reason="", warning="")

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_api_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.source_dir = self.root / "资料"
        self.source_dir.mkdir()
        (self.source_dir / "note.md").write_text(
            "# 项目\n蓝鲸计划是今年的重点项目。", encoding="utf-8"
        )
        self.service = KnowledgeService(
            self.root, embedder=FakeEmbedder(), autostart=False
        )
        self.app = create_app(
            root=self.root,
            token="t",
            knowledge_service=self.service,
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def _wait_idle(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.client.get(
                "/api/knowledge/status", headers=self.headers
            ).json()
            if not status["indexing"] and status["state"] in {
                "idle",
                "done",
                "error",
                "cancelled",
            }:
                return status
            time.sleep(0.02)
        raise AssertionError("indexing did not finish")

    def test_add_source_indexes_and_search_finds_content(self):
        response = self.client.post(
            "/api/knowledge/sources",
            json={"path": str(self.source_dir)},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        status = self._wait_idle()
        self.assertGreater(status["chunks"], 0)

        result = self.client.get(
            "/api/knowledge/search",
            params={"q": "蓝鲸", "top_k": 3},
            headers=self.headers,
        ).json()

        self.assertEqual(result["results"][0]["path"], "note.md")

    def test_relative_source_is_rejected(self):
        response = self.client.post(
            "/api/knowledge/sources",
            json={"path": "relative"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_toggle_disables_search_and_tool(self):
        self.service.set_enabled(False)
        result = self.client.get(
            "/api/knowledge/search",
            params={"q": "蓝鲸"},
            headers=self.headers,
        ).json()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "disabled")

        tool = self.service.tools()[0]
        self.assertIn("未启用", tool.invoke({"query": "蓝鲸"}))

    def test_remove_source_clears_index(self):
        self.client.post(
            "/api/knowledge/sources",
            json={"path": str(self.source_dir)},
            headers=self.headers,
        )
        self._wait_idle()
        source_id = self.client.get(
            "/api/knowledge/status", headers=self.headers
        ).json()["sources"][0]["id"]

        response = self.client.delete(
            f"/api/knowledge/sources/{source_id}", headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertEqual(status["sources"], [])
        self.assertEqual(status["documents"], 0)

    def test_empty_query_is_rejected(self):
        response = self.client.get(
            "/api/knowledge/search",
            params={"q": "  "},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_api
```

Expected: FAIL because `create_app` has no `knowledge_service` parameter and the routes are missing.

- [ ] **Step 3: Wire the service into `create_app`**

Add imports at the top of `desktop_api.py`:

```python
from knowledge_service import KnowledgeBusyError, KnowledgeService
from knowledge_store import KnowledgeError
```

Add the parameters:

```python
def create_app(
    root: Path,
    token: str,
    graph_builder=build_graph,
    mcp_manager=None,
    data_root: Path | None = None,
    workspace: Path | None = None,
    model_factory=None,
    knowledge_service=None,
    knowledge_autostart: bool = True,
) -> FastAPI:
```

After the `mcp` assignment inside `create_app`, add:

```python
    knowledge = knowledge_service or KnowledgeService(
        data_dir, autostart=knowledge_autostart
    )
```

Add a helper near `_extra_tools`:

```python
    def _model_extra_tools(key: str):
        try:
            profile = get_profile(key)
        except ModelConfigError:
            profile = None
        tools = list(knowledge.tools())
        if profile is None or profile.provider != "ollama":
            tools = tools + _extra_tools()
        return tools
```

In `run_graph.attempt`, replace:

```python
            extra_tools = [] if profile.provider == "ollama" else _extra_tools()
```

with:

```python
            extra_tools = _model_extra_tools(key)
```

Also update the two places that measure the fallback or manual-summary budget
so the knowledge tool schema is included:

- In the `ensure_summary` call inside `attempt`, replace
  `_tool_tokens_for(fallback.key)` with
  `_tool_tokens_for(fallback.key, _model_extra_tools(fallback.key))`.
- In `compact_now`, replace `_tool_tokens_for(target.key)` with
  `_tool_tokens_for(target.key, _model_extra_tools(target.key))`.

In `_context_json`, replace `_tool_tokens_for(key, _extra_tools())` with
`_tool_tokens_for(key, _model_extra_tools(key))`.

Add the endpoints inside `create_app`, after the `/api/models` endpoint:

```python
    @app.get("/api/knowledge/status", dependencies=[Depends(require_token)])
    def knowledge_status() -> dict:
        return knowledge.status()

    @app.post("/api/knowledge/sources", dependencies=[Depends(require_token)])
    def add_knowledge_source(payload: dict) -> dict:
        path = str(payload.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=400, detail="文件夹路径不能为空")
        try:
            return knowledge.add_source(path)
        except KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(
        "/api/knowledge/sources/{source_id}",
        dependencies=[Depends(require_token)],
    )
    def remove_knowledge_source(source_id: int) -> dict:
        try:
            return knowledge.remove_source(source_id)
        except KnowledgeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/knowledge/index", dependencies=[Depends(require_token)])
    def index_knowledge(payload: dict | None = None) -> dict:
        rebuild = _as_bool((payload or {}).get("rebuild"), default=False)
        try:
            status = knowledge.start_index(rebuild=rebuild)
        except KnowledgeBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**status, "started": True}

    @app.post("/api/knowledge/cancel", dependencies=[Depends(require_token)])
    def cancel_knowledge() -> dict:
        return knowledge.cancel()

    @app.patch("/api/knowledge/settings", dependencies=[Depends(require_token)])
    def knowledge_settings(payload: dict) -> dict:
        if "enabled" in payload:
            return knowledge.set_enabled(_as_bool(payload.get("enabled")))
        return knowledge.status()

    @app.get("/api/knowledge/search", dependencies=[Depends(require_token)])
    def knowledge_search(q: str = "", top_k: int = 5) -> dict:
        query = q.strip()
        if not query:
            raise HTTPException(status_code=400, detail="查询不能为空")
        return knowledge.search(query, top_k=top_k)
```

Add startup and shutdown hooks next to the MCP hooks:

```python
    @app.on_event("startup")
    def start_knowledge() -> None:
        knowledge.startup()

    @app.on_event("shutdown")
    def stop_knowledge() -> None:
        knowledge.shutdown()
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_knowledge_api
```

Expected: PASS. If the search returns no result, verify the indexer finished and `chunks > 0`; the fake embedder returns identical vectors, so the lexical path should still match with jieba.

- [ ] **Step 5: Run the existing backend suite**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest discover -s tests -v
```

Expected: PASS. The new knowledge database is created per temporary data dir; no existing test should be affected. If `test_import_does_not_initialize_data_dir` fails, ensure the knowledge store is only constructed inside `create_app`, not at module import.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop_api.py tests/test_knowledge_api.py
git commit -m "feat: expose knowledge base API and agent tool"
```

---

### Task 8: Electron folder picker

**Files:**
- Modify: `Langgraph/desktop/preload.js`
- Modify: `Langgraph/desktop/main.js`
- Modify: `Langgraph/desktop/src/global.d.ts`
- Test: `Langgraph/desktop/settings.test.cjs` (no change needed) and a small main-process test is not available; verify manually and via the frontend mock.

- [ ] **Step 1: Add the IPC handler**

In `desktop/main.js`, next to the other `ipcMain.handle` registrations (after `desktop:save-models`), add:

```js
ipcMain.handle("desktop:pick-folder", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
    title: "选择知识库文件夹",
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});
```

- [ ] **Step 2: Expose it to the renderer**

In `desktop/preload.js`, add to the exposed object:

```js
  pickFolder: () => ipcRenderer.invoke("desktop:pick-folder"),
```

- [ ] **Step 3: Update the window type**

In `desktop/src/global.d.ts`, add to the `desktop` object:

```ts
    pickFolder(): Promise<string | null>;
```

- [ ] **Step 4: Verify the Electron files parse**

```bash
cd D:/Vibecoding/Langgraph/desktop
node --check main.js
node --check preload.js
npx tsc --noEmit
```

Expected: no syntax or type errors.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop/main.js desktop/preload.js desktop/src/global.d.ts
git commit -m "feat: add a native folder picker for the knowledge base"
```

---

### Task 9: Frontend API, panel, and settings tab

**Files:**
- Modify: `Langgraph/desktop/src/types.ts`
- Modify: `Langgraph/desktop/src/api.ts`
- Create: `Langgraph/desktop/src/components/KnowledgePanel.tsx`
- Modify: `Langgraph/desktop/src/components/SettingsDialog.tsx`
- Modify: `Langgraph/desktop/src/styles.css`
- Test: `Langgraph/desktop/src/components/KnowledgePanel.test.tsx`
- Test: `Langgraph/desktop/src/components/SettingsDialog.test.tsx`

- [ ] **Step 1: Add types and API functions**

In `desktop/src/types.ts`, add:

```ts
export interface KnowledgeSource {
  id: number;
  path: string;
  document_count: number;
  enabled: boolean;
}

export interface KnowledgeProgress {
  total_files: number;
  processed_files: number;
  indexed_documents: number;
  failed_documents: number;
  current_file: string;
  started_at: string;
  finished_at: string;
  message: string;
}

export interface KnowledgeStatus {
  enabled: boolean;
  indexing: boolean;
  state: string;
  started?: boolean;
  progress: KnowledgeProgress;
  sources: KnowledgeSource[];
  documents: number;
  chunks: number;
  last_index_at: string;
  embedding: {
    model: string;
    available: boolean;
    reason: string;
    warning: string;
    dim: number;
  };
}

export interface KnowledgeSearchResult {
  chunk_id: number;
  path: string;
  absolute_path: string;
  heading: string;
  page: number | null;
  sheet: string;
  slide: number | null;
  score: number;
  text: string;
}

export interface KnowledgeSearchResponse {
  query: string;
  enabled: boolean;
  embedding_used: boolean;
  warning: string;
  results: KnowledgeSearchResult[];
  reason: string;
}
```

In `desktop/src/api.ts`, import the new types and add:

```ts
export async function getKnowledgeStatus(): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/status");
}

export async function addKnowledgeSource(path: string): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/sources", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function removeKnowledgeSource(id: number): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>(`/api/knowledge/sources/${id}`, {
    method: "DELETE",
  });
}

export async function startKnowledgeIndex(
  rebuild = false,
): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/index", {
    method: "POST",
    body: JSON.stringify({ rebuild }),
  });
}

export async function cancelKnowledgeIndex(): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/cancel", { method: "POST" });
}

export async function setKnowledgeEnabled(enabled: boolean): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>("/api/knowledge/settings", {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export async function searchKnowledge(
  q: string,
  topK = 5,
): Promise<KnowledgeSearchResponse> {
  const params = new URLSearchParams({ q, top_k: String(topK) });
  return request<KnowledgeSearchResponse>(`/api/knowledge/search?${params}`);
}
```

- [ ] **Step 2: Implement `KnowledgePanel.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import {
  addKnowledgeSource,
  cancelKnowledgeIndex,
  getKnowledgeStatus,
  removeKnowledgeSource,
  setKnowledgeEnabled,
  startKnowledgeIndex,
} from "../api";
import { errorText, formatCount } from "../helpers";
import type { KnowledgeStatus } from "../types";

export function KnowledgePanel() {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const timerRef = useRef<number | null>(null);

  async function refresh(): Promise<void> {
    try {
      setStatus(await getKnowledgeStatus());
    } catch (cause) {
      setMessage(errorText(cause));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    const interval = status?.indexing ? 1_000 : 10_000;
    timerRef.current = window.setInterval(() => void refresh(), interval);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [status?.indexing]);

  async function run(action: () => Promise<KnowledgeStatus>, success = ""): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setStatus(await action());
      if (success) setMessage(success);
    } catch (cause) {
      setMessage(errorText(cause));
    } finally {
      setBusy(false);
    }
  }

  async function addFolder(): Promise<void> {
    const path = await window.desktop?.pickFolder();
    if (!path) return;
    await run(() => addKnowledgeSource(path), "已添加，正在建立索引");
  }

  if (!status) {
    return <div className="knowledge-panel">{message || "正在读取知识库状态…"}</div>;
  }

  const progress = status.progress;
  const total = progress.total_files || 0;
  const done = progress.processed_files || 0;
  const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  const embedding = status.embedding;

  return (
    <div className="knowledge-panel">
      <label className="model-checkbox">
        <input
          type="checkbox"
          checked={status.enabled}
          disabled={busy}
          onChange={(event) =>
            void run(() => setKnowledgeEnabled(event.target.checked))
          }
        />
        启用知识库（Agent 会自动检索）
      </label>

      <div className={`knowledge-status ${embedding.available ? "ok" : "error"}`}>
        嵌入模型：{embedding.model} ·{" "}
        {embedding.available ? "已就绪" : embedding.reason || "不可用"}
      </div>
      {embedding.warning && <div className="knowledge-warning">{embedding.warning}</div>}
      <div className="knowledge-note">
        使用云端模型时，检索到的资料会作为上下文发送给云端模型；需要完全离线请切换到本地模型。
      </div>

      <div className="knowledge-actions">
        <button type="button" onClick={() => void addFolder()} disabled={busy}>
          添加文件夹
        </button>
        <button
          type="button"
          onClick={() => void run(() => startKnowledgeIndex(false), "已开始更新索引")}
          disabled={busy || status.indexing}
        >
          更新索引
        </button>
        <button
          type="button"
          onClick={() => {
            if (!window.confirm("清空并重建整个知识库索引？")) return;
            void run(() => startKnowledgeIndex(true), "已开始重建索引");
          }}
          disabled={busy || status.indexing}
        >
          重建索引
        </button>
        {status.indexing && (
          <button
            type="button"
            onClick={() => void run(() => cancelKnowledgeIndex(), "已请求取消")}
            disabled={busy}
          >
            取消
          </button>
        )}
      </div>

      {status.sources.length === 0 && (
        <div className="empty-hint">还没有添加文件夹</div>
      )}
      {status.sources.map((source) => (
        <div className="knowledge-source" key={source.id}>
          <div>
            <strong>{source.path}</strong>
            <span>{source.document_count} 个文档</span>
          </div>
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => {
              if (!window.confirm(`移除 ${source.path}？`)) return;
              void run(() => removeKnowledgeSource(source.id));
            }}
          >
            移除
          </button>
        </div>
      ))}

      {status.indexing && (
        <div className="knowledge-progress">
          <div className="knowledge-progress-bar">
            <span style={{ width: `${percent}%` }} />
          </div>
          <div className="knowledge-progress-text">
            {progress.current_file || "准备中"} · {done}/{total} 个文件
          </div>
        </div>
      )}
      <div className="knowledge-counts">
        <span>文档 {formatCount(status.documents)}</span>
        <span>分块 {formatCount(status.chunks)}</span>
        <span>失败 {progress.failed_documents}</span>
        <span>最后索引 {status.last_index_at || "从未"}</span>
      </div>
      {progress.message && <div className="knowledge-warning">{progress.message}</div>}
      {message && <div className="setup-message">{message}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Add the settings tab**

In `desktop/src/components/SettingsDialog.tsx`:

- Import `KnowledgePanel`.
- Add `const [tab, setTab] = useState<"models" | "knowledge">("models");` next to the other state.
- Change the heading to `设置 · {tab === "models" ? "模型" : "知识库"}`.
- Add tabs after the heading:

```tsx
        <div className="settings-tabs">
          <button
            type="button"
            className={tab === "models" ? "active" : ""}
            onClick={() => setTab("models")}
          >
            模型
          </button>
          <button
            type="button"
            className={tab === "knowledge" ? "active" : ""}
            onClick={() => setTab("knowledge")}
          >
            知识库
          </button>
        </div>
```

- Wrap the existing model content from `<p className="settings-hint">` through the model `</div>` in `{tab === "models" && (<> ... </>)}`.
- Add `{tab === "knowledge" && <KnowledgePanel />}` before the modal actions.
- Change the submit handler to only save models when `tab === "models"`:

```tsx
        onSubmit={(event) => {
          event.preventDefault();
          if (tab === "models") void saveAll();
        }}
```

- Show the bottom "保存" button only in the models tab:

```tsx
          {tab === "models" && (
            <button type="submit" className="primary" disabled={busy || !!draft}>
              保存
            </button>
          )}
```

- [ ] **Step 4: Add CSS**

Append to `desktop/src/styles.css`:

```css
.settings-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.settings-tabs button { padding: 5px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); color: var(--muted); cursor: pointer; }
.settings-tabs button.active { color: var(--text); border-color: var(--accent); }
.knowledge-panel { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
.knowledge-status { font-size: 12px; color: var(--muted); }
.knowledge-status.ok { color: var(--text); }
.knowledge-status.error { color: var(--danger, #b3261e); }
.knowledge-warning { padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; color: var(--danger, #b3261e); font-size: 12px; }
.knowledge-note { font-size: 12px; color: var(--muted); }
.knowledge-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.knowledge-source { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--panel-2); }
.knowledge-source div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.knowledge-source strong { font-size: 12px; word-break: break-all; }
.knowledge-source span { font-size: 11px; color: var(--muted); }
.knowledge-progress-bar { height: 8px; border-radius: 4px; background: var(--panel-2); overflow: hidden; }
.knowledge-progress-bar span { display: block; height: 100%; background: var(--accent); }
.knowledge-progress-text { margin-top: 4px; font-size: 11px; color: var(--muted); word-break: break-all; }
.knowledge-counts { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; color: var(--muted); }
```

- [ ] **Step 5: Write the frontend tests**

Create `desktop/src/components/KnowledgePanel.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => {
  const status = {
    enabled: true,
    indexing: false,
    state: "done",
    progress: {
      total_files: 2,
      processed_files: 2,
      indexed_documents: 2,
      failed_documents: 0,
      current_file: "",
      started_at: "",
      finished_at: "",
      message: "",
    },
    sources: [{ id: 1, path: "D:/资料", document_count: 2, enabled: true }],
    documents: 2,
    chunks: 8,
    last_index_at: "2026-11-02T10:00:00+08:00",
    embedding: {
      model: "qwen3-embedding:0.6b",
      available: true,
      reason: "",
      warning: "",
      dim: 1024,
    },
  };
  return {
    getKnowledgeStatus: vi.fn(async () => status),
    addKnowledgeSource: vi.fn(async () => status),
    removeKnowledgeSource: vi.fn(async () => status),
    startKnowledgeIndex: vi.fn(async () => ({ ...status, started: true })),
    cancelKnowledgeIndex: vi.fn(async () => status),
    setKnowledgeEnabled: vi.fn(async () => ({ ...status, enabled: false })),
  };
});

import { KnowledgePanel } from "./KnowledgePanel";
import {
  addKnowledgeSource,
  setKnowledgeEnabled,
  startKnowledgeIndex,
} from "../api";

beforeEach(() => {
  window.desktop = {
    pickFolder: vi.fn(async () => "D:/资料"),
  } as unknown as Window["desktop"];
});

afterEach(cleanup);

describe("KnowledgePanel", () => {
  it("adds a folder through the native picker", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("添加文件夹"));

    await waitFor(() =>
      expect(addKnowledgeSource).toHaveBeenCalledWith("D:/资料"),
    );
  });

  it("toggles the global switch", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByLabelText(/启用知识库/));

    await waitFor(() =>
      expect(setKnowledgeEnabled).toHaveBeenCalledWith(false),
    );
  });

  it("starts an incremental index", async () => {
    render(<KnowledgePanel />);
    fireEvent.click(await screen.findByText("更新索引"));

    await waitFor(() => expect(startKnowledgeIndex).toHaveBeenCalledWith(false));
  });
});
```

Add to `desktop/src/components/SettingsDialog.test.tsx` a tab test (mock `../api` with the knowledge functions):

```tsx
  it("opens the knowledge tab", async () => {
    const api = await import("../api");
    vi.spyOn(api, "getKnowledgeStatus").mockResolvedValue({
      enabled: true,
      indexing: false,
      state: "idle",
      progress: {
        total_files: 0,
        processed_files: 0,
        indexed_documents: 0,
        failed_documents: 0,
        current_file: "",
        started_at: "",
        finished_at: "",
        message: "",
      },
      sources: [],
      documents: 0,
      chunks: 0,
      last_index_at: "",
      embedding: {
        model: "qwen3-embedding:0.6b",
        available: true,
        reason: "",
        warning: "",
        dim: 1024,
      },
    });
    render(<SettingsDialog onClose={() => undefined} onSaved={() => undefined} />);
    fireEvent.click(screen.getByText("知识库"));
    expect(await screen.findByText("添加文件夹")).toBeTruthy();
  });
```

Note: if `SettingsDialog.test.tsx` does not import `fireEvent`/`vi`, add the missing imports.

- [ ] **Step 6: Run the frontend tests and type check**

```bash
cd D:/Vibecoding/Langgraph/desktop
npx vitest run src/components/KnowledgePanel.test.tsx src/components/SettingsDialog.test.tsx
npx tsc --noEmit
```

Expected: PASS and no type errors.

- [ ] **Step 7: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop/src/types.ts desktop/src/api.ts desktop/src/components/KnowledgePanel.tsx \
  desktop/src/components/SettingsDialog.tsx desktop/src/styles.css \
  desktop/src/components/KnowledgePanel.test.tsx desktop/src/components/SettingsDialog.test.tsx
git commit -m "feat: add the knowledge base settings panel"
```

---

### Task 10: Dependencies, full suites, and manual verification

**Files:**
- Modify: `Langgraph/requirements-desktop.txt`

- [ ] **Step 1: Add the dependencies**

Append to `requirements-desktop.txt`:

```text
numpy>=1.26
jieba>=0.42
```

- [ ] **Step 2: Run the full backend suite**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 3: Run the full frontend suite**

```bash
cd D:/Vibecoding/Langgraph/desktop
npm test
npx tsc --noEmit
```

Expected: PASS.

- [ ] **Step 4: Manual verification with the development app**

1. Confirm Ollama has the embedding model:

```bash
ollama list | grep qwen3-embedding
```

If it is missing, run `ollama pull qwen3-embedding:0.6b` manually; the app never downloads it automatically.

2. Start the development app (do not package):

```bash
cd D:/Vibecoding/Langgraph/desktop
npm run dev
```

3. Open Settings → 知识库:
   - Add a folder containing `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx`, and `.csv` files.
   - Confirm indexing progress, document/chunk counts, and that the embedding model shows ready.
4. Ask a question whose answer exists only in that folder. Confirm the agent calls `search_knowledge_base`, the tool card shows sources, and the answer cites a source path.
5. Modify one file and delete another; click 更新索引; confirm the changed document is reindexed and the deleted one disappears.
6. Stop Ollama; confirm the status panel shows an embedding error, indexing stops, and search falls back to lexical-only with a warning.
7. Turn the global switch off; confirm `search_knowledge_base` returns the disabled message.
8. Use a local chat model and repeat step 4; confirm retrieval works offline.
9. Optionally, click 重建索引 and confirm it clears and rebuilds.

No packaging, installer, or installed-application changes.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add requirements-desktop.txt
git commit -m "chore: add numpy and jieba for the knowledge base"
```

---

## Self-Review Checklist

- Spec coverage:
  - SQLite store, FTS5, vector blobs, settings: Task 1.
  - Discovery, formats, chunking, metadata: Task 2.
  - Local Ollama embeddings, availability, loopback warning: Task 3.
  - Vector plus lexical RRF, disabled/empty/no-match handling: Task 4.
  - Background indexing, incremental scan, cancel, capacity, atomic document replace: Task 5.
  - Agent tool, core tool whitelist, prompt rules: Task 6.
  - API endpoints, runtime wiring, startup/shutdown: Task 7.
  - Electron folder picker: Task 8.
  - Settings tab, panel, polling, switch, progress, privacy note: Task 9.
  - numpy/jieba, full suites, manual verification: Task 10.
- No placeholders: every step contains concrete code and commands.
- Type consistency: `KnowledgeStatus`, `KnowledgeSource`, `KnowledgeProgress`, `ChunkInsert`, `KnowledgeService`, `KnowledgeSearcher.search`, and `build_knowledge_tools` keep the same names across tasks.
- Known limitation carried from the spec: no file watcher, no OCR, no reranker, no cloud embedding fallback.
