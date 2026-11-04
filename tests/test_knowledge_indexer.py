from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_embed import EmbeddingStatus
from knowledge_indexer import KnowledgeIndexer, _Cancelled
from knowledge_store import KnowledgeStore


class FakeEmbedder:
    model = "fake-embed"

    def status(self):
        return EmbeddingStatus(model=self.model, available=True, reason="", warning="")

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class CancelEmbedder(FakeEmbedder):
    def embed(self, texts):
        raise _Cancelled()


class CountingEmbedder(FakeEmbedder):
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class RemovingEmbedder(FakeEmbedder):
    def __init__(self, store, source_id):
        self.store = store
        self.source_id = source_id

    def embed(self, texts):
        self.store.remove_source(self.source_id)
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

    def test_cancellation_during_embedding_marks_the_run_cancelled(self):
        (self.source_dir / "a.md").write_text("# 概述\n内容。", encoding="utf-8")
        self.indexer = KnowledgeIndexer(self.store, CancelEmbedder())

        self.indexer.start()
        state = self._wait()

        self.assertEqual(state["state"], "cancelled")

    def test_oversized_document_is_skipped_before_embedding(self):
        (self.source_dir / "huge.md").write_text(
            "# 概述\n" + "内容。" * 2000, encoding="utf-8"
        )
        embedder = CountingEmbedder()
        self.indexer = KnowledgeIndexer(self.store, embedder)
        with patch("knowledge_indexer.MAX_CHUNKS_PER_DOCUMENT", 2):
            self.indexer.start()
            state = self._wait()

        self.assertEqual(embedder.calls, 0)
        self.assertEqual(self.store.count_chunks(), 0)
        document = self.store.list_documents()[0]
        self.assertEqual(document.status, "error")
        self.assertIn("文件过大", document.error)
        self.assertEqual(state["failed_documents"], 1)

    def test_source_removed_during_embedding_stops_without_fk(self):
        (self.source_dir / "a.md").write_text(
            "# 概述\n" + "内容。" * 400, encoding="utf-8"
        )
        self.indexer = KnowledgeIndexer(
            self.store, RemovingEmbedder(self.store, self.source.id)
        )

        self.indexer.start()
        state = self._wait()

        self.assertEqual(state["state"], "error")
        self.assertIn("来源已被移除", state["message"])
        self.assertEqual(self.store.count_documents(), 0)
        self.assertEqual(self.store.count_chunks(), 0)
