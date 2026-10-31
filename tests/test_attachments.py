"""附件落盘：保存、去重、读取与格式校验。"""

from __future__ import annotations

import base64
import shutil
import unittest
import uuid
from pathlib import Path

from attachments import AttachmentError, AttachmentStore

WORKSPACE = Path(__file__).resolve().parent.parent

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
)  # 只需能通过签名嗅探
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


class AttachmentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_attachments_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = AttachmentStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_returns_reference_and_writes_file(self):
        ref = self.store.save_data_url(data_url(PNG_BYTES), "photo.png")

        self.assertEqual(ref["type"], "image_ref")
        self.assertEqual(len(ref["attachment_id"]), 64)
        self.assertEqual(ref["name"], "photo.png")
        self.assertTrue(self.store.exists(ref["attachment_id"]))

    def test_same_image_is_stored_once(self):
        first = self.store.save_data_url(data_url(PNG_BYTES))
        second = self.store.save_data_url(data_url(PNG_BYTES))

        self.assertEqual(first["attachment_id"], second["attachment_id"])
        self.assertEqual(len(list(self.store.root.iterdir())), 1)

    def test_read_returns_bytes_and_mime(self):
        ref = self.store.save_data_url(data_url(JPEG_BYTES, "image/jpeg"))

        raw, mime = self.store.read(ref["attachment_id"])

        self.assertEqual(raw, JPEG_BYTES)
        self.assertEqual(mime, "image/jpeg")

    def test_data_url_roundtrip(self):
        ref = self.store.save_data_url(data_url(PNG_BYTES))

        restored = self.store.data_url(ref["attachment_id"])

        self.assertTrue(restored.startswith("data:image/png;base64,"))
        self.assertEqual(
            base64.b64decode(restored.split(",", 1)[1]), PNG_BYTES
        )

    def test_rejects_non_image_and_malformed_input(self):
        with self.assertRaises(AttachmentError):
            self.store.save_data_url("data:text/plain;base64,aGk=")
        with self.assertRaises(AttachmentError):
            self.store.save_data_url("not-a-data-url")

    def test_rejects_invalid_id(self):
        with self.assertRaises(AttachmentError):
            self.store.read("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
