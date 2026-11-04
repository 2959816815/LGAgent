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
                "embedded_chunks": progress["embedded_chunks"],
                "total_chunks": progress["total_chunks"],
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
        if self.indexer.is_running():
            self.indexer.cancel()
            self.indexer.wait(timeout=10.0)
        self.store.add_source(path)
        if not self.indexer.is_running():
            self.indexer.start(rebuild=False)
        return self.status()

    def remove_source(self, source_id: int) -> dict[str, Any]:
        if self.indexer.is_running():
            self.indexer.cancel()
            self.indexer.wait(timeout=10.0)
        self.store.remove_source(source_id)
        if (
            self.store.enabled
            and self.store.list_sources()
            and not self.indexer.is_running()
        ):
            self.indexer.start(rebuild=False)
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
