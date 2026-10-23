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


def _markdown_blocks(content: str) -> list[tuple[str, Any]]:
    blocks: list[tuple[str, Any]] = []
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue

        if line.lstrip().startswith("|"):
            raw_table: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                raw_table.append(lines[index].strip())
                index += 1
            rows = _parse_markdown_table(raw_table)
            if rows:
                blocks.append(("table", rows))
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            blocks.append(("heading", (len(heading.group(1)), heading.group(2).strip())))
            index += 1
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            blocks.append(("bullet", bullet.group(1).strip()))
            index += 1
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if numbered:
            blocks.append(("numbered", numbered.group(1).strip()))
            index += 1
            continue

        blocks.append(("paragraph", line.strip()))
        index += 1
    return blocks


def _parse_markdown_table(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if index == 1 and cells and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in cells
        ):
            continue
        rows.append(cells)
    return rows


def _iter_docx_paragraphs(doc: Any) -> Iterator[Any]:
    seen: list[Any] = []

    def emit(paragraphs: Iterator[Any]) -> Iterator[Any]:
        for paragraph in paragraphs:
            element = paragraph._p
            if any(element is existing for existing in seen):
                continue
            seen.append(element)
            yield paragraph

    yield from emit(iter(doc.paragraphs))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from emit(iter(cell.paragraphs))


def _replace_in_docx_paragraph(
    paragraph: Any,
    old_text: str,
    new_text: str,
    replace_all: bool,
) -> tuple[int, bool]:
    if old_text not in paragraph.text:
        return 0, False

    replaced = 0
    for run in paragraph.runs:
        if old_text not in run.text:
            continue
        if replace_all:
            count = run.text.count(old_text)
            run.text = run.text.replace(old_text, new_text)
            replaced += count
        else:
            run.text = run.text.replace(old_text, new_text, 1)
            replaced += 1
            break

    if replaced:
        return replaced, False

    original = paragraph.text
    updated = original.replace(old_text, new_text, -1 if replace_all else 1)
    count = original.count(old_text)
    if paragraph.runs:
        first = paragraph.runs[0]
        for run in paragraph.runs[1:]:
            run._element.getparent().remove(run._element)
        first.text = updated
    else:
        paragraph.add_run(updated)
    return count if replace_all else 1, True


@tool
def read_docx(path: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """读取 .docx 的正文段落和表格文字，返回带段落/表格编号的文本。"""
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    error = _validate_existing(target, ".docx")
    if error:
        return error

    limit = _coerce_max_chars(max_chars)
    try:
        doc = Document(str(target))
    except Exception as exc:
        return f"[错误] 打开 Word 失败: {target} ({exc})"

    lines = [f"[文档] {target}"]
    for index, paragraph in enumerate(doc.paragraphs, 1):
        text = paragraph.text.strip()
        if text:
            lines.append(f"[段落 {index}] {text}")

    for table_index, table in enumerate(doc.tables, 1):
        lines.append(f"[表格 {table_index}]")
        for row_index, row in enumerate(table.rows, 1):
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            lines.append(f"  行 {row_index}: " + " | ".join(cells))

    return _truncate("\n".join(lines), limit)


@tool
def write_docx(path: str, content: str) -> str:
    """新建或整体覆盖 .docx。content 支持 Markdown 标题、列表、段落和管道表格。"""
    if not isinstance(content, str):
        return "[错误] content 必须是字符串。"
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    error = _validate_write_target(target, ".docx")
    if error:
        return error
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return f"[错误] 内容过大，上限 {_MAX_WRITE_BYTES} 字节"

    try:
        doc = Document()
        for kind, value in _markdown_blocks(content):
            if kind == "heading":
                level, text = value
                doc.add_heading(text, level=level)
            elif kind == "bullet":
                doc.add_paragraph(value, style="List Bullet")
            elif kind == "numbered":
                doc.add_paragraph(value, style="List Number")
            elif kind == "table":
                rows = value
                table = doc.add_table(rows=len(rows), cols=max(len(row) for row in rows))
                table.style = "Table Grid"
                for row_index, row in enumerate(rows):
                    for col_index, text in enumerate(row):
                        table.cell(row_index, col_index).text = text
            else:
                doc.add_paragraph(value)
        buffer = io.BytesIO()
        doc.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 Word 失败: {exc}"

    return _write_office_target(target, buffer.getvalue())


@tool
def edit_docx(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """在 .docx 中把 old_text 精确替换为 new_text；默认要求唯一匹配。"""
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "[错误] old_text 和 new_text 必须是字符串。"
    if not old_text:
        return "[错误] old_text 不能为空。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".docx")
    if error:
        return error

    try:
        doc = Document(str(target))
    except Exception as exc:
        return f"[错误] 打开 Word 失败: {target} ({exc})"

    paragraphs = list(_iter_docx_paragraphs(doc))
    total = sum(paragraph.text.count(old_text) for paragraph in paragraphs)
    if total == 0:
        return f"[错误] 未找到 old_text: {target}"
    if total > 1 and not replace_all:
        return (
            f"[错误] old_text 出现 {total} 次，请提供更长的唯一片段，"
            "或设置 replace_all=true。"
        )

    replaced = 0
    warnings = 0
    for paragraph in paragraphs:
        if old_text not in paragraph.text:
            continue
        if not replace_all and replaced:
            break
        count, rebuilt = _replace_in_docx_paragraph(
            paragraph, old_text, new_text, replace_all
        )
        replaced += count
        warnings += int(rebuilt)

    if replaced == 0:
        return f"[错误] 未找到可替换的文本: {target}"

    try:
        buffer = io.BytesIO()
        doc.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 Word 失败: {target} ({exc})"

    result = _write_office_target(target, buffer.getvalue())
    result = result.replace("[写入]", "[修改]")
    if warnings:
        result += f"\n[提示] {warnings} 处跨格式片段已按段落样式重建"
    return result
