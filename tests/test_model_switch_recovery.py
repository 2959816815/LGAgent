"""会话里记录的模型已不存在时的回退行为。"""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from desktop_api import create_app
from session_store import SessionStore

WORKSPACE = Path(__file__).resolve().parent.parent

MODELS = json.dumps(
    [
        {
            "id": "myapi",
            "kind": "api",
            "label": "我的 API",
            "base_url": "https://example.com/v1",
            "model": "foo-1",
            "api_key": "sk-test",
        }
    ]
)


class EchoGraph:
    def stream(self, payload, *args, **kwargs):
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="好的")]})


class StaleModelKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_stale_model_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SessionStore(self.root / ".sessions")
        self.session = self.store.create("旧会话", model_key="qwen")
        self.store.save(self.session)
        self.headers = {"X-Desktop-Token": "t"}

    def _client(self) -> TestClient:
        app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: EchoGraph(),
        )
        return TestClient(app)

    def test_detail_reports_an_available_model(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": MODELS}):
            detail = self._client().get(
                f"/api/sessions/{self.session.id}", headers=self.headers
            ).json()

        self.assertEqual(detail["model_key"], "myapi")

    def test_sending_falls_back_and_persists(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": MODELS}):
            client = self._client()
            response = client.post(
                f"/api/sessions/{self.session.id}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200)
            detail = client.get(
                f"/api/sessions/{self.session.id}", headers=self.headers
            ).json()

        self.assertEqual(detail["model_key"], "myapi")


if __name__ == "__main__":
    unittest.main()
