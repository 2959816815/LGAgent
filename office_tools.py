r"""Word / Excel / PPT / PDF tools for graph_agent.

All tools are self-contained: they do not import graph_agent, so there is no
circular import.  They use python-docx, openpyxl, python-pptx and pypdf.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from langchain_core.tools import tool
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pypdf import PdfReader

_MAX_OFFICE_BYTES = 50_000_000
_MAX_WRITE_BYTES = 5_000_000
_DEFAULT_MAX_CHARS = 20_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 50_000
_MAX_ROWS = 200
_MAX_COLS = 30
_MAX_BACKUP_INDEX = 99
_MAX_PDF_PAGES = 50


def _resolve_path(raw_path: str) -> Path:
    """Expand ~, environment variables and relative paths."""
    if not isinstance(raw_path, str):
        raise ValueError("路径必须是字符串")
    cleaned = raw_path.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        raise ValueError("路径为空")
    expanded = os.path.expandvars(os.path.expanduser(cleaned))
    return Path(os.path.abspath(expanded)).resolve()


def _check_suffix(target: Path, suffix: str) -> str | None:
    if target.suffix.lower() != suffix:
        return f"[错误] 只支持 {suffix} 文件: {target}"
    return None


def _validate_existing(target: Path, suffix: str) -> str | None:
    error = _check_suffix(target, suffix)
    if error:
        return error
    if not target.exists():
        return f"[错误] 文件不存在: {target}"
    if not target.is_file():
        return f"[错误] 目标不是普通文件: {target}"
    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"[错误] 读取文件信息失败: {target} ({exc})"
    if size > _MAX_OFFICE_BYTES:
        return f"[错误] 文件过大: {size} 字节，上限 {_MAX_OFFICE_BYTES} 字节"
    return None


def _validate_write_target(target: Path, suffix: str) -> str | None:
    error = _check_suffix(target, suffix)
    if error:
        return error
    parent = target.parent
    if not parent.exists() or not parent.is_dir():
        return f"[错误] 父目录不存在: {parent}"
    if target.exists() and target.is_dir():
        return f"[错误] 目标是目录: {target}"
    if target.exists():
        try:
            size = target.stat().st_size
        except OSError as exc:
            return f"[错误] 读取文件信息失败: {target} ({exc})"
        if size > _MAX_OFFICE_BYTES:
            return f"[错误] 文件过大: {size} 字节，上限 {_MAX_OFFICE_BYTES} 字节"
    return None


def _coerce_max_chars(value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = _DEFAULT_MAX_CHARS
    return min(max(number, _MIN_MAX_CHARS), _MAX_MAX_CHARS)


def _coerce_limit(value: int, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 1), maximum)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[已截断]"


def _next_backup_path(target: Path) -> Path:
    candidate = target.with_name(target.name + ".bak")
    if not candidate.exists():
        return candidate
    for index in range(1, _MAX_BACKUP_INDEX + 1):
        candidate = target.with_name(f"{target.name}.bak.{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("备份文件过多")


def _backup_existing(target: Path) -> Path:
    backup = _next_backup_path(target)
    shutil.copy2(target, backup)
    return backup


def _atomic_write(target: Path, data: bytes) -> None:
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _write_office_target(target: Path, data: bytes) -> str:
    if len(data) > _MAX_OFFICE_BYTES:
        return f"[错误] 生成文件过大: {len(data)} 字节，上限 {_MAX_OFFICE_BYTES} 字节"
    existed = target.exists()
    backup: Path | None = None
    if existed:
        try:
            backup = _backup_existing(target)
        except OSError as exc:
            return f"[错误] 备份失败: {target} ({exc})"
    try:
        _atomic_write(target, data)
    except OSError as exc:
        return f"[错误] 写入失败: {target} ({exc})"
    action = "[写入]" if existed else "[新建]"
    lines = [f"{action} {target} ({len(data)} 字节)"]
    if backup is not None:
        lines.append(f"[备份] {backup}")
    return "\n".join(lines)
