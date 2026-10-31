"""GET /api/models 的可用性判断。"""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from desktop_api import create_app

WORKSPACE = Path(__file__).resolve().parent.parent


def models_json(api_key: str = "") -> str:
    return json.dumps(
        [
            {
                "id": "mylocal",
                "kind": "local",
                "label": "本地模型",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen3.5:2b",
            },
            {
                "id": "myapi",
                "kind": "api",
                "label": "我的 API",
                "base_url": "https://example.com/v1",
                "model": "foo-1",
                "api_key": api_key,
            },
        ],
        ensure_ascii=False,
    )


class ModelsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_models_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.client = TestClient(create_app(root=self.root, token="t"))
        self.headers = {"X-Desktop-Token": "t"}

    def _models(self) -> list[dict]:
        return self.client.get("/api/models", headers=self.headers).json()["models"]

    def test_local_unreachable_reports_reason(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": models_json("sk-1")}), patch(
            "desktop_api._probe_local",
            return_value=(False, "Ollama 未启动或地址不可达"),
        ):
            models = self._models()

        local = next(item for item in models if item["key"] == "mylocal")
        self.assertFalse(local["available"])
        self.assertEqual(local["reason"], "Ollama 未启动或地址不可达")

    def test_api_model_without_key_is_unavailable(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": models_json("")}), patch(
            "desktop_api._probe_local", return_value=(True, "")
        ):
            models = self._models()

        api = next(item for item in models if item["key"] == "myapi")
        self.assertFalse(api["available"])
        self.assertIn("API Key", api["reason"])

    def test_api_model_with_key_is_available(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": models_json("sk-1")}), patch(
            "desktop_api._probe_local", return_value=(True, "")
        ):
            models = self._models()

        api = next(item for item in models if item["key"] == "myapi")
        self.assertTrue(api["available"])
        self.assertNotIn("reason", api)


if __name__ == "__main__":
    unittest.main()
