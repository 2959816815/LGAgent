"""secret_store.py — 用 Windows DPAPI 加密 mcp_servers.json 里的密钥。

DPAPI（CryptProtectData / CryptUnprotectData）按当前 Windows 用户加密，
与 Electron safeStorage 在 Windows 上用的是同一套底层机制，但这里不依赖 Electron。

约定：加密后的值以 ``dpapi:`` 开头（base64 编码的密文）。非 Windows 平台
或调用失败时退回明文，保证功能不被破坏。
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import sys
from ctypes import wintypes

PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def available() -> bool:
    """当前平台是否支持 DPAPI。"""
    return sys.platform == "win32"


def is_protected(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _crypt(data: bytes, decrypt: bool) -> bytes | None:
    source = _blob(data)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "langgraph-mcp",
            None,
            None,
            None,
            0,
            ctypes.byref(result),
        )
    if not ok:
        return None
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def protect(text: str) -> str:
    """加密明文；已加密或平台不支持时原样返回。"""
    if not text or not available() or is_protected(text):
        return text
    blob = _crypt(text.encode("utf-8"), decrypt=False)
    if blob is None:
        return text
    return PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect(value: str) -> str:
    """解密；非密文原样返回，解密失败返回空串。"""
    if not is_protected(value):
        return value
    if not available():
        return ""
    try:
        raw = base64.b64decode(value[len(PREFIX) :], validate=True)
    except (ValueError, binascii.Error):
        return ""
    text = _crypt(raw, decrypt=True)
    return text.decode("utf-8", errors="replace") if text is not None else ""
