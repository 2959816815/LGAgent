from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from desktop_api import _message_json, create_app
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
        self.store = SessionStore(self.root / ".sessions")

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

    def test_session_rename(self):
        created = self.client.post(
            "/api/sessions", json={"title": "旧标题"}, headers=self.headers
        ).json()
        response = self.client.patch(
            f"/api/sessions/{created['id']}",
            json={"title": "新标题"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "新标题")
        detail = self.client.get(f"/api/sessions/{created['id']}", headers=self.headers)
        self.assertEqual(detail.json()["title"], "新标题")

    def test_search_sessions_finds_message_and_title(self):
        session = self.store.create("蓝鲸项目周报")
        session.messages.extend(
            [
                HumanMessage(content="本周完成登录接口，下一步处理支付"),
                AIMessage(content="支付模块计划周五上线"),
            ]
        )
        self.store.save(session)

        by_message = self.client.get(
            "/api/sessions/search", params={"q": "支付"}, headers=self.headers
        )
        self.assertEqual(by_message.status_code, 200)
        hits = by_message.json()["results"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["session_id"], session.id)
        self.assertIn("支付", hits[0]["snippet"])
        self.assertEqual(hits[0]["role"], "human")

        by_title = self.client.get(
            "/api/sessions/search", params={"q": "蓝鲸"}, headers=self.headers
        )
        self.assertEqual(by_title.json()["results"][0]["role"], "title")

    def test_search_sessions_is_case_insensitive_and_empty_safe(self):
        session = self.store.create("API Notes")
        session.messages.append(HumanMessage(content="Deploy the API tomorrow"))
        self.store.save(session)
        response = self.client.get(
            "/api/sessions/search", params={"q": "deploy"}, headers=self.headers
        )
        self.assertIn("Deploy", response.json()["results"][0]["snippet"])
        empty = self.client.get(
            "/api/sessions/search", params={"q": "   "}, headers=self.headers
        )
        self.assertEqual(empty.json()["results"], [])

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
            self.assertIn("context_window", item)
            self.assertIn("supports_vision", item)


class WorkspaceFilesApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_desktop_files_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "spec.md").write_text("spec", encoding="utf-8")
        (self.root / ".hidden.txt").write_text("hidden", encoding="utf-8")
        self.app = create_app(root=self.root, token="t")
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def test_lists_root_and_subdir(self):
        root = self.client.get("/api/files", headers=self.headers)
        self.assertEqual(root.status_code, 200)
        names = [item["name"] for item in root.json()["entries"]]
        self.assertEqual(names, ["docs", "notes.txt"])

        sub = self.client.get("/api/files?path=docs", headers=self.headers)
        self.assertEqual(sub.status_code, 200)
        self.assertEqual(sub.json()["entries"][0]["path"], "docs/spec.md")

    def test_rejects_path_escape(self):
        response = self.client.get("/api/files?path=..", headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_search_matches_file_name(self):
        response = self.client.get("/api/files/search?q=spec", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["path"], "docs/spec.md")


class FakeChunk(AIMessageChunk):
    pass


class FakeGraph:
    def __init__(self):
        self.cancelled = False

    def stream(self, *args, **kwargs):
        yield ("messages", (FakeChunk(content="你"), {}))
        yield ("messages", (FakeChunk(content="好"), {}))
        yield ("updates", {"agent": {"messages": [AIMessage(content="你好")]}})
        yield ("values", {"messages": [HumanMessage(content="hi"), AIMessage(content="你好")]})


class BlockingGraph:
    def __init__(self, release):
        self.release = release

    def stream(self, *args, **kwargs):
        yield ("messages", (FakeChunk(content="等"), {}))
        self.release.wait(timeout=2)


class McpApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.client = TestClient(create_app(root=self.root, token="t"))
        self.headers = {"X-Desktop-Token": "t"}

    def test_catalog_requires_token(self):
        self.assertEqual(self.client.get("/api/mcp/catalog").status_code, 401)

    def test_catalog_contains_sqlite(self):
        response = self.client.get("/api/mcp/catalog", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["presets"]}
        self.assertIn("sqlite", ids)
        self.assertIn("github", ids)

    def test_unknown_preset_is_400(self):
        response = self.client.post(
            "/api/mcp/servers",
            json={"preset_id": "missing-server"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)


class StreamingApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_desktop_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: FakeGraph(),
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def test_message_stream_tokens_and_done(self):
        session = self.client.post(
            "/api/sessions", json={"title": "流式"}, headers=self.headers
        ).json()
        response = self.client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "hi"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("event: token", body)
        self.assertIn('"text": "你"', body)
        self.assertIn("event: done", body)

    def test_session_unlocks_before_done_stream_closes(self):
        """收到 done 后立刻发下一条消息，不应再报“已有回复正在生成”。"""
        session = self.client.post(
            "/api/sessions", json={"title": "连续发送"}, headers=self.headers
        ).json()
        with self.client.stream(
            "POST",
            f"/api/sessions/{session['id']}/messages",
            json={"text": "第一条"},
            headers=self.headers,
        ) as response:
            self.assertEqual(response.status_code, 200)
            for line in response.iter_lines():
                if line.startswith("event: done"):
                    break
            # 故意不关闭流，模拟前端收到 done 后马上发下一条。
            second = self.client.post(
                f"/api/sessions/{session['id']}/messages",
                json={"text": "第二条"},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.text)
            self.assertIn("event: done", second.text)

    def test_another_session_is_not_blocked_by_active_run(self):
        release = threading.Event()
        app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: BlockingGraph(release),
        )
        client = TestClient(app)
        first = client.post(
            "/api/sessions", json={"title": "第一条"}, headers=self.headers
        ).json()
        second = client.post(
            "/api/sessions", json={"title": "第二条"}, headers=self.headers
        ).json()

        with client.stream(
            "POST",
            f"/api/sessions/{first['id']}/messages",
            json={"text": "慢任务"},
            headers=self.headers,
        ) as first_response:
            self.assertEqual(first_response.status_code, 200)
            with client.stream(
                "POST",
                f"/api/sessions/{second['id']}/messages",
                json={"text": "新会话"},
                headers=self.headers,
            ) as second_response:
                self.assertEqual(second_response.status_code, 200)
                release.set()
        release.set()

    def test_graph_setup_failure_does_not_lock_session(self):
        app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        client = TestClient(app)
        session = client.post(
            "/api/sessions", json={"title": "失败"}, headers=self.headers
        ).json()
        first = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "触发失败"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertIn("event: error", first.text)
        self.assertIn("boom", first.text)
        self.assertIn("event: done", first.text)
        second = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "再次发送"},
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 200)

    def test_image_is_rejected_for_local_model(self):
        session = self.client.post(
            "/api/sessions", json={"title": "本地"}, headers=self.headers
        ).json()
        response = self.client.post(
            f"/api/sessions/{session['id']}/messages",
            json={
                "text": "",
                "attachments": [
                    {"data_url": "data:image/png;base64,iVBORw0KGgo="},
                ],
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不支持图片", response.json()["detail"])

    def test_image_is_accepted_for_qwen_model(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}):
            session = self.client.post(
                "/api/sessions",
                json={"title": "看图", "model_key": "qwen"},
                headers=self.headers,
            ).json()
            response = self.client.post(
                f"/api/sessions/{session['id']}/messages",
                json={
                    "text": "看看这张图",
                    "attachments": [
                        {"data_url": "data:image/png;base64,iVBORw0KGgo="},
                    ],
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: done", response.text)

    def test_message_json_keeps_image_attachments(self):
        data = _message_json(
            HumanMessage(
                content=[
                    {"type": "text", "text": "看图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ]
            )
        )
        self.assertEqual(data["content"], "看图")
        self.assertEqual(data["attachments"][0]["data_url"], "data:image/png;base64,abc")


if __name__ == "__main__":
    unittest.main()
