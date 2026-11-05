from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from desktop_api import _as_bool, _message_json, create_app
from knowledge_service import KnowledgeService
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


class DesktopDataDirTests(unittest.TestCase):
    def test_data_root_controls_sessions_and_mcp(self):
        root = Path(__file__).resolve().parent.parent / f".test_desktop_api_{uuid.uuid4().hex[:8]}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        data = root / "userdata"
        data.mkdir()
        captured = {}

        class FakeStore:
            def __init__(self, path):
                captured["sessions"] = Path(path)

        class FakeMcp:
            def __init__(self, path):
                captured["mcp"] = Path(path)

        with patch("desktop_api.SessionStore", FakeStore), patch("desktop_api.McpManager", FakeMcp):
            create_app(root=root, token="t", graph_builder=lambda: None, data_root=data)

        self.assertEqual(captured["sessions"], data / ".sessions")
        self.assertEqual(captured["mcp"], data)


class DesktopWorkspaceTests(unittest.TestCase):
    def test_files_use_workspace_not_code_root(self):
        base = Path(__file__).resolve().parent.parent / f".test_workspace_{uuid.uuid4().hex[:8]}"
        base.mkdir()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)

        code_root = base / "app"
        code_root.mkdir()
        (code_root / "internal_only.txt").write_text("internal", encoding="utf-8")

        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "user_file.txt").write_text("hello", encoding="utf-8")

        app = create_app(root=code_root, token="t", workspace=workspace)
        client = TestClient(app)
        payload = client.get("/api/files", headers={"X-Desktop-Token": "t"}).json()
        names = [entry["name"] for entry in payload["entries"]]

        self.assertIn("user_file.txt", names)
        self.assertNotIn("internal_only.txt", names)


class LazyAppTests(unittest.TestCase):
    def test_import_does_not_initialize_data_dir(self):
        import subprocess
        import sys

        project = Path(__file__).resolve().parent.parent
        base = project / f".test_lazy_{uuid.uuid4().hex[:8]}"
        base.mkdir()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)

        env = os.environ.copy()
        env["DSH_DATA_DIR"] = str(base)
        env["PYTHONPATH"] = str(project)

        result = subprocess.run(
            [sys.executable, "-c", "import desktop_api"],
            cwd=str(project),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            (base / ".sessions").exists(),
            "导入模块不应初始化会话存储（打包后的 worker/MCP 子进程会因此重复初始化）",
        )


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

    def test_agents_can_be_filtered_by_session(self):
        from subagent_store import SubagentStore

        store = SubagentStore(self.root / ".agents")
        first = store.create("任务 A", session_id="s1")
        store.create("任务 B", session_id="s2")

        everything = self.client.get("/api/agents", headers=self.headers).json()["agents"]
        self.assertEqual(len(everything), 2)

        only = self.client.get("/api/agents?session_id=s1", headers=self.headers).json()["agents"]
        self.assertEqual([item["id"] for item in only], [first.id])
        self.assertEqual(only[0]["session_id"], "s1")

    def test_cancel_unknown_agent_returns_404(self):
        response = self.client.post(
            "/api/agents/agent-20260101-000000-abcd/cancel", headers=self.headers
        )
        self.assertEqual(response.status_code, 404)

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
        # 知识库数据库不能落在工作区里，否则会出现在文件面板中。
        kb_root = Path(tempfile.mkdtemp(prefix="test_desktop_files_kb_"))
        self.addCleanup(shutil.rmtree, kb_root, ignore_errors=True)
        self.app = create_app(
            root=self.root,
            token="t",
            knowledge_service=KnowledgeService(kb_root, autostart=False),
        )
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

    def stream(self, payload, *args, **kwargs):
        yield ("messages", (FakeChunk(content="你"), {}))
        yield ("messages", (FakeChunk(content="好"), {}))
        yield ("updates", {"agent": {"messages": [AIMessage(content="你好")]}})
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="你好")]})


class BlockingGraph:
    def __init__(self, release):
        self.release = release

    def stream(self, *args, **kwargs):
        yield ("messages", (FakeChunk(content="等"), {}))
        self.release.wait(timeout=2)


class HangingGraph:
    """一直挂起，直到测试显式 release；用于验证残留 run 的处理。"""

    def __init__(self, release):
        self.release = release

    def stream(self, *args, **kwargs):
        yield ("messages", (FakeChunk(content="等"), {}))
        self.release.wait()


class EchoGraph:
    """把输入消息原样带回，方便断言重生成/编辑后的会话内容。"""

    def stream(self, payload, *args, **kwargs):
        messages = payload["messages"]
        yield ("messages", (FakeChunk(content="重"), {}))
        yield ("values", {"messages": [*messages, AIMessage(content="重来")]})


class BoolParsingTests(unittest.TestCase):
    def test_string_values_are_parsed_strictly(self):
        self.assertFalse(_as_bool("false"))
        self.assertFalse(_as_bool("0"))
        self.assertTrue(_as_bool("true"))
        self.assertTrue(_as_bool("yes"))
        self.assertTrue(_as_bool(True))
        self.assertFalse(_as_bool(None))
        self.assertTrue(_as_bool("", default=True))
        self.assertFalse(_as_bool("随便", default=False))


class DuplicateToolGraph:
    """同一个 ToolMessage 同时出现在 messages 和 updates 两个流里。"""

    def stream(self, payload, *args, **kwargs):
        tool = ToolMessage(content="完成", tool_call_id="call-1", name="read_file")
        yield ("messages", (tool, {}))
        yield ("updates", {"tools": {"messages": [tool]}})
        yield (
            "values",
            {"messages": [*payload["messages"], AIMessage(content="好的")]},
        )


class ToolEventDedupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_tool_end_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: DuplicateToolGraph(),
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def test_tool_end_is_emitted_once_per_call(self):
        session = self.client.post(
            "/api/sessions", json={"title": "工具"}, headers=self.headers
        ).json()

        response = self.client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "读文件"},
            headers=self.headers,
        )

        self.assertEqual(response.text.count("event: tool_end"), 1)


class ConversationEditApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_edit_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: EchoGraph(),
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def _create(self) -> str:
        return self.client.post(
            "/api/sessions", json={"title": "编辑"}, headers=self.headers
        ).json()["id"]

    def _send(self, sid: str, body: dict):
        return self.client.post(
            f"/api/sessions/{sid}/messages", json=body, headers=self.headers
        )

    def _messages(self, sid: str) -> list[dict]:
        return self.client.get(f"/api/sessions/{sid}", headers=self.headers).json()["messages"]

    def test_regenerate_reuses_last_user_message(self):
        sid = self._create()
        self._send(sid, {"text": "原始问题"})
        self.assertEqual([m["role"] for m in self._messages(sid)], ["user", "assistant"])

        response = self._send(sid, {"mode": "regenerate"})

        self.assertEqual(response.status_code, 200)
        after = self._messages(sid)
        self.assertEqual([m["role"] for m in after], ["user", "assistant"])
        self.assertEqual(after[0]["content"], "原始问题")

    def test_edit_replaces_user_message_and_drops_later_turns(self):
        sid = self._create()
        self._send(sid, {"text": "原始问题"})
        self._send(sid, {"text": "第二个问题"})
        self.assertEqual(len(self._messages(sid)), 4)

        response = self._send(sid, {"text": "改过的问题", "edit_index": 0})

        self.assertEqual(response.status_code, 200)
        after = self._messages(sid)
        self.assertEqual(len(after), 2)
        self.assertEqual([m["role"] for m in after], ["user", "assistant"])
        self.assertEqual(after[0]["content"], "改过的问题")

    def test_session_detail_supports_paging(self):
        sid = self._create()
        for text in ["第一问", "第二问", "第三问"]:
            self._send(sid, {"text": text})
        self.assertEqual(len(self._messages(sid)), 6)

        first = self.client.get(
            f"/api/sessions/{sid}", params={"limit": 2}, headers=self.headers
        ).json()
        self.assertEqual(len(first["messages"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_before"], 4)

        second = self.client.get(
            f"/api/sessions/{sid}", params={"limit": 2, "before": 4}, headers=self.headers
        ).json()
        self.assertEqual(len(second["messages"]), 2)
        self.assertTrue(second["has_more"])
        self.assertEqual(second["next_before"], 2)

        last = self.client.get(
            f"/api/sessions/{sid}", params={"limit": 2, "before": 2}, headers=self.headers
        ).json()
        self.assertEqual(len(last["messages"]), 2)
        self.assertFalse(last["has_more"])
        self.assertEqual(last["next_before"], 0)

    def test_edit_rejects_non_user_message(self):
        sid = self._create()
        self._send(sid, {"text": "问题"})

        response = self._send(sid, {"text": "x", "edit_index": 1})

        self.assertEqual(response.status_code, 400)


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

    def test_409_includes_run_id_and_cancel_unlocks_session(self):
        release = threading.Event()
        app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: HangingGraph(release),
        )
        client = TestClient(app)
        # 第二个客户端用于在第一条流未结束时并发发请求。
        client2 = TestClient(app)
        self.addCleanup(release.set)
        session = client.post(
            "/api/sessions", json={"title": "取消"}, headers=self.headers
        ).json()
        # TestClient 会缓冲整个流，所以第一条请求放到后台线程里跑，
        # 主线程趁它还没结束时发第二条。
        first_result = {}

        def first_request() -> None:
            first_result["response"] = client.post(
                f"/api/sessions/{session['id']}/messages",
                json={"text": "第一条"},
                headers=self.headers,
            )

        thread = threading.Thread(target=first_request, daemon=True)
        thread.start()
        time.sleep(0.5)

        blocked = client2.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "第二条"},
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 409)
        run_id = blocked.json()["detail"]["run_id"]
        self.assertTrue(run_id)

        cancelled = client2.post(f"/api/runs/{run_id}/cancel", headers=self.headers)
        self.assertEqual(cancelled.status_code, 200)

        # 取消后同一会话应立刻可以继续发，不必等旧线程退出。
        release.set()
        second = client2.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "第三条"},
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        thread.join(timeout=5)
        self.assertEqual(first_result["response"].status_code, 200)

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
