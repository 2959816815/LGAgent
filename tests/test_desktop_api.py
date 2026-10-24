from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from desktop_api import create_app
from session_store import SessionStore


class SessionModelKeyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_desktop_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SessionStore(self.root / ".sessions")

    def test_create_and_load_model_key(self):
        session = self.store.create("桌面端", model_key="deepseek")
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual(loaded.model_key, "deepseek")

    def test_old_session_without_model_key_is_compatible(self):
        session = self.store.create("旧会话")
        path = self.root / ".sessions" / f"{session.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("model_key", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = self.store.load(session.id)
        self.assertIsNone(loaded.model_key)
        self.assertIsNone(self.store.list_sessions()[0].model_key)

    def test_meta_exposes_model_key(self):
        self.store.create("会话 A", model_key="qwen")
        meta = self.store.list_sessions()[0]
        self.assertEqual(meta.model_key, "qwen")


class DesktopSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_desktop_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app = create_app(root=self.root, token="test-token")
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "test-token"}

    def test_health_does_not_require_token(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_session_crud(self):
        created = self.client.post(
            "/api/sessions",
            json={"title": "测试会话", "model_key": "local"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["id"]
        self.assertEqual(created.json()["model_key"], "local")

        listed = self.client.get("/api/sessions", headers=self.headers)
        self.assertEqual(len(listed.json()["sessions"]), 1)

        detail = self.client.get(f"/api/sessions/{session_id}", headers=self.headers)
        self.assertEqual(detail.json()["title"], "测试会话")

        deleted = self.client.delete(f"/api/sessions/{session_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)

    def test_session_requires_token(self):
        response = self.client.get("/api/sessions")
        self.assertEqual(response.status_code, 401)

    def test_model_patch_persists(self):
        created = self.client.post(
            "/api/sessions",
            json={"title": "模型测试"},
            headers=self.headers,
        ).json()
        response = self.client.patch(
            f"/api/sessions/{created['id']}/model",
            json={"model_key": "deepseek"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/api/sessions/{created['id']}", headers=self.headers)
        self.assertEqual(detail.json()["model_key"], "deepseek")

    def test_unknown_model_is_400(self):
        created = self.client.post(
            "/api/sessions", json={"title": "模型测试"}, headers=self.headers
        ).json()
        response = self.client.patch(
            f"/api/sessions/{created['id']}/model",
            json={"model_key": "nope"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_models_do_not_leak_keys(self):
        response = self.client.get("/api/models", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        for item in response.json()["models"]:
            self.assertNotIn("api_key", item)


if __name__ == "__main__":
    unittest.main()
