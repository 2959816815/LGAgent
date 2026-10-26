"""Tests for HTTP browsing/download tools."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_tools


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://example.com/page",
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
    ) -> None:
        self._body = body
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.history: list = []
        self.closed = False

    @property
    def is_redirect(self) -> bool:
        return False

    def iter_content(self, chunk_size: int = 65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class WebToolsTests(unittest.TestCase):
    def test_html_to_text_skips_scripts(self) -> None:
        html = (
            "<html><head><title>测试 页面</title>"
            "<style>.x{color:red}</style></head>"
            "<body><h1>标题</h1><p>第一段</p>"
            "<script>alert('x')</script><p>第二段</p></body></html>"
        )
        title, body = web_tools._html_to_text(html)
        self.assertIn("测试 页面", title)
        self.assertIn("标题", body)
        self.assertIn("第一段", body)
        self.assertIn("第二段", body)
        self.assertNotIn("alert", body)
        self.assertNotIn("color:red", body)

    def test_web_fetch_extracts_page(self) -> None:
        response = FakeResponse(
            "<html><head><title>示例</title></head><body><p>正文内容</p></body></html>".encode()
        )
        with patch.object(web_tools, "_open_response", return_value=response):
            result = web_tools.web_fetch.invoke(
                {"url": "https://example.com/page", "max_chars": 1000}
            )
        self.assertIn("[网页] 示例", result)
        self.assertIn("正文内容", result)
        self.assertTrue(response.closed)

    def test_web_fetch_rejects_binary(self) -> None:
        response = FakeResponse(b"PK\x03\x04", content_type="application/octet-stream")
        with patch.object(web_tools, "_open_response", return_value=response):
            result = web_tools.web_fetch.invoke({"url": "https://example.com/a.zip"})
        self.assertIn("[错误]", result)
        self.assertIn("download_file", result)

    def test_web_fetch_rejects_private_address(self) -> None:
        result = web_tools.web_fetch.invoke({"url": "http://127.0.0.1:8000/secret"})
        self.assertIn("[错误]", result)
        self.assertIn("内网", result)

    def test_web_fetch_rejects_file_scheme(self) -> None:
        result = web_tools.web_fetch.invoke({"url": "file:///C:/Windows/win.ini"})
        self.assertIn("[错误]", result)
        self.assertIn("http/https", result)

    def test_download_file_writes_and_hashes(self) -> None:
        payload = b"fake software bytes"
        response = FakeResponse(
            payload,
            url="https://example.com/files/tool.zip",
            content_type="application/zip",
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "tool.zip"
            with patch.object(web_tools, "_open_response", return_value=response):
                result = web_tools.download_file.invoke(
                    {"url": "https://example.com/files/tool.zip", "path": str(target)}
                )
            self.assertIn("[下载]", result)
            self.assertEqual(target.read_bytes(), payload)
            self.assertIn(hashlib.sha256(payload).hexdigest(), result)
        self.assertTrue(response.closed)

    def test_download_file_requires_overwrite(self) -> None:
        response = FakeResponse(b"new", url="https://example.com/a.bin")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "a.bin"
            target.write_bytes(b"old")
            with patch.object(web_tools, "_open_response", return_value=response):
                result = web_tools.download_file.invoke(
                    {"url": "https://example.com/a.bin", "path": str(target)}
                )
            self.assertIn("已存在", result)
            self.assertEqual(target.read_bytes(), b"old")
        self.assertTrue(response.closed)

    def test_download_file_overwrite(self) -> None:
        response = FakeResponse(b"new", url="https://example.com/a.bin")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "a.bin"
            target.write_bytes(b"old")
            with patch.object(web_tools, "_open_response", return_value=response):
                result = web_tools.download_file.invoke(
                    {
                        "url": "https://example.com/a.bin",
                        "path": str(target),
                        "overwrite": True,
                    }
                )
            self.assertIn("[下载]", result)
            self.assertEqual(target.read_bytes(), b"new")
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
