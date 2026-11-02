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
