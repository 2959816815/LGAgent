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
