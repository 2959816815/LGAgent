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
