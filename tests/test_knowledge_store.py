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
