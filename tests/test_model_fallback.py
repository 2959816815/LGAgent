"""Automatic local fallback and health endpoint tests."""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from desktop_api import create_app
from session_store import SessionStore


class NetworkErrorGraph:
    def stream(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")
        yield  # pragma: no cover


class StaticErrorGraph:
    def __init__(self, exc: Exception):
        self.exc = exc

    def stream(self, *args, **kwargs):
        raise self.exc
        yield  # pragma: no cover


class LocalGraph:
    def stream(self, payload, *args, **kwargs):
        yield ("messages", (AIMessageChunk(content="本地"), {}))
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="本地回答")]})


class FallbackApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_fallback_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "test-key", "OLLAMA_CONTEXT_WINDOW": "16384"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = SessionStore(self.root / ".sessions")
        self.headers = {"X-Desktop-Token": "t"}

    def _session(self):
        return self.store.create("降级", model_key="deepseek").id

    def _app(self, builder):
        return create_app(root=self.root, token="t", graph_builder=builder)

    def test_network_error_falls_back_and_retries_once(self):
        sid = self._session()
        calls: list[str] = []

        def builder(manager, model_key, extra_tools=None):
            calls.append(model_key)
            return NetworkErrorGraph() if model_key == "deepseek" else LocalGraph()

        client = TestClient(self._app(builder))
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: model_fallback", response.text)
        self.assertIn('"model_key": "local"', response.text)
        self.assertEqual(calls, ["deepseek", "local"])

        detail = client.get(f"/api/sessions/{sid}", headers=self.headers).json()
        self.assertEqual(detail["model_key"], "local")
        self.assertEqual([m["role"] for m in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][-1]["content"], "本地回答")

    def test_auth_error_does_not_fall_back(self):
        sid = self._session()
        client = TestClient(
            self._app(
                lambda manager, model_key, extra_tools=None: StaticErrorGraph(
                    RuntimeError("401 Unauthorized")
                )
            )
        )
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertNotIn("event: model_fallback", response.text)
        self.assertIn("event: error", response.text)
        detail = client.get(f"/api/sessions/{sid}", headers=self.headers).json()
        self.assertEqual(detail["model_key"], "deepseek")

    def test_local_unavailable_does_not_fall_back(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: NetworkErrorGraph())
        )
        with patch("desktop_api._probe_local", return_value=(False, "Ollama 未启动")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertNotIn("event: model_fallback", response.text)
        self.assertIn("event: error", response.text)

    def test_retry_failure_reports_both_causes(self):
        sid = self._session()

        def builder(manager, model_key, extra_tools=None):
            if model_key == "deepseek":
                return NetworkErrorGraph()
            return StaticErrorGraph(RuntimeError("local boom"))

        client = TestClient(self._app(builder))
        with patch("desktop_api._probe_local", return_value=(True, "")):
            response = client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "继续"},
                headers=self.headers,
            )

        self.assertIn("event: model_fallback", response.text)
        self.assertIn("本地模型重试也失败", response.text)

    def test_local_health_uses_local_probe(self):
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )
        with patch("desktop_api._probe_local", return_value=(True, "")):
            body = client.get("/api/models/local/health", headers=self.headers).json()

        self.assertTrue(body["reachable"])
        self.assertEqual(body["key"], "local")

    def test_cloud_health_uses_tcp_probe(self):
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )
        with patch("desktop_api._probe_cloud", return_value=(False, "无法连接")):
            body = client.get("/api/models/deepseek/health", headers=self.headers).json()

        self.assertFalse(body["reachable"])

    def test_session_detail_exposes_context(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )

        body = client.get(f"/api/sessions/{sid}", headers=self.headers).json()

        self.assertIn("context", body)
        self.assertEqual(body["context"]["window"], 128000)

    def test_model_switch_returns_context_preview(self):
        sid = self._session()
        client = TestClient(
            self._app(lambda manager, model_key, extra_tools=None: LocalGraph())
        )

        body = client.patch(
            f"/api/sessions/{sid}/model",
            json={"model_key": "local"},
            headers=self.headers,
        ).json()

        self.assertEqual(body["model_key"], "local")
        self.assertEqual(body["context"]["window"], 16384)
