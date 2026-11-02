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
