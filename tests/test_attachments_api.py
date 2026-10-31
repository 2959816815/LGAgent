"""附件落盘的端到端行为：落盘、引用、取图与鉴权。"""

from __future__ import annotations

import base64
import shutil
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from desktop_api import create_app

WORKSPACE = Path(__file__).resolve().parent.parent
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


class CapturingGraph:
    def __init__(self) -> None:
        self.seen: list = []

    def stream(self, payload, *args, **kwargs):
        self.seen = list(payload["messages"])
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="好的")]})


class AttachmentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_attach_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.graph = CapturingGraph()
        self.app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: self.graph,
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def _session(self) -> str:
        # 图片要求视觉模型，这里用 qwen
        return self.client.post(
            "/api/sessions", json={"title": "图", "model_key": "qwen"}, headers=self.headers
        ).json()["id"]

    def _send(self, session_id: str, **extra):
        return self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"text": "看图", **extra},
            headers=self.headers,
        )

    def _attachment_url(self, session_id: str) -> str:
        detail = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        ).json()
        return detail["messages"][0]["attachments"][0]["data_url"]

    def test_attachment_is_stored_on_disk_and_referenced(self):
        session_id = self._session()

        response = self._send(
            session_id,
            attachments=[{"data_url": data_url(PNG_BYTES), "name": "a.png"}],
        )

        self.assertEqual(response.status_code, 200)
        files = list((self.root / "attachments").iterdir())
        self.assertEqual(len(files), 1)

        url = self._attachment_url(session_id)
        self.assertIn("/api/attachments/", url)
        self.assertIn("token=t", url)

        stored = (self.root / ".sessions" / f"{session_id}.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("base64", stored, "会话文件里不应再存 base64")

        human = next(m for m in self.graph.seen if getattr(m, "type", "") == "human")
        part = human.content[1]
        self.assertEqual(part["type"], "image_url")
        self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_attachment_endpoint_serves_bytes_and_requires_token(self):
        session_id = self._session()
        self._send(session_id, attachments=[{"data_url": data_url(PNG_BYTES)}])
        url = self._attachment_url(session_id)
        path = "/" + url.split("/", 3)[3]

        ok = self.client.get(path)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, PNG_BYTES)
        self.assertEqual(ok.headers["content-type"], "image/png")

        denied = self.client.get(path.split("?")[0])
        self.assertEqual(denied.status_code, 401)

    def test_legacy_inline_image_still_renders(self):
        session_id = self._session()
        # 直接写入一条旧格式（base64 内联）消息
        from session_store import SessionStore
        from langchain_core.messages import HumanMessage

        store = SessionStore(self.root / ".sessions")
        session = store.load(session_id)
        session.messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "旧图"},
                    {"type": "image_url", "image_url": {"url": data_url(PNG_BYTES)}},
                ]
            )
        ]
        store.save(session)

        detail = self.client.get(
            f"/api/sessions/{session_id}", headers=self.headers
        ).json()
        attachment = detail["messages"][0]["attachments"][0]

        self.assertTrue(attachment["data_url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
