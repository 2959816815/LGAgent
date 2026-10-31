"""attachments.py — 聊天图片落盘。

图片按 sha256 存到 ``<data_dir>/attachments/<id>``，消息里只保留引用
（``{"type": "image_ref", "attachment_id": ..., "name": ...}``）。
发给模型前再用 :meth:`AttachmentStore.data_url` 还原成 data URL——
视觉接口只接受 data URL / http URL，读不到本地路径。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from pathlib import Path

ATTACHMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class AttachmentError(Exception):
    """附件解析或存储错误。"""


def sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def parse_data_url(value: str) -> tuple[str, bytes]:
    """拆分 data URL，返回 (mime, 原始字节)；只接受图片。"""
    text = (value or "").strip()
    if not text.startswith("data:"):
        raise AttachmentError("不是合法的 data URL")
    header, separator, payload = text.partition(",")
    if not separator or not header.endswith(";base64"):
        raise AttachmentError("只支持 base64 编码的 data URL")
    mime = header[len("data:") : -len(";base64")].strip().lower()
    if not mime.startswith("image/"):
        raise AttachmentError("只支持图片附件")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError(f"图片内容无法解码: {exc}") from exc
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("图片太大（上限 10MB）")
    return mime, raw


class AttachmentStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "attachments"

    def _path(self, attachment_id: str) -> Path:
        if not ATTACHMENT_ID_RE.fullmatch(attachment_id or ""):
            raise AttachmentError("非法的附件 ID")
        return self.root / attachment_id

    def save_data_url(self, value: str, name: str = "") -> dict:
        """把 data URL 写入文件，返回消息里保存的引用片段。"""
        _, raw = parse_data_url(value)
        attachment_id = hashlib.sha256(raw).hexdigest()
        path = self.root / attachment_id
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(raw)
            os.replace(temp, path)
        return {
            "type": "image_ref",
            "attachment_id": attachment_id,
            "name": name or "",
        }

    def exists(self, attachment_id: str) -> bool:
        try:
            return self._path(attachment_id).is_file()
        except AttachmentError:
            return False

    def read(self, attachment_id: str) -> tuple[bytes, str]:
        path = self._path(attachment_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise AttachmentError(f"附件不存在: {attachment_id}") from exc
        return raw, sniff_mime(raw)

    def data_url(self, attachment_id: str) -> str:
        raw, mime = self.read(attachment_id)
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
