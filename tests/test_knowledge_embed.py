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
