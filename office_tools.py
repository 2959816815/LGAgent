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


def _coerce_excel_value(raw: str) -> Any:
    text = raw
    stripped = text.strip()
    if stripped.startswith("="):
        return stripped
    if stripped == "":
        return None
    if re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", stripped):
        if "." in stripped:
            return float(stripped)
        return int(stripped)
    return text


def _validate_sheet_name(name: str) -> str | None:
    if not name or len(name) > 31:
        return "[错误] sheet_name 不能为空，且最长 31 个字符"
    if re.search(r"[\[\]:*?/\\]", name):
        return "[错误] sheet_name 不能包含 [ ] : * ? / \\ 等字符"
    return None


@tool
def read_xlsx(
    path: str,
    max_rows: int = 200,
    max_cols: int = 30,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """读取 .xlsx 的工作表、单元格值和公式，返回带坐标的文本。"""
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".xlsx")
    if error:
        return error

    row_limit = _coerce_limit(max_rows, 200, _MAX_ROWS)
    col_limit = _coerce_limit(max_cols, 30, _MAX_COLS)
    char_limit = _coerce_max_chars(max_chars)

    try:
        workbook = load_workbook(str(target), data_only=False, read_only=True)
    except Exception as exc:
        return f"[错误] 打开 Excel 失败: {target} ({exc})"

    lines = [f"[工作簿] {target}"]
    try:
        for sheet in workbook.worksheets:
            total_rows = sheet.max_row or 0
            total_cols = sheet.max_column or 0
            last_row = min(total_rows, row_limit)
            last_col = min(total_cols, col_limit)
            dimension = (
                f"A1:{get_column_letter(last_col)}{last_row}"
                if last_row and last_col
                else "A1"
            )
            lines.append(f"[工作表] {sheet.title} ({dimension})")
            if not last_row or not last_col:
                continue
            for row in sheet.iter_rows(
                min_row=1,
                max_row=last_row,
                min_col=1,
                max_col=last_col,
            ):
                cells = [
                    f"{cell.coordinate}: {cell.value}"
                    for cell in row
                    if cell.value is not None
                ]
                if cells:
                    lines.append(" | ".join(cells))
            if total_rows > row_limit or total_cols > col_limit:
                lines.append(
                    f"[提示] 仅显示前 {row_limit} 行 / {col_limit} 列"
                )
    finally:
        workbook.close()
    return _truncate("\n".join(lines), char_limit)


@tool
def write_xlsx(
    path: str,
    content: str,
    sheet_name: str = "Sheet1",
) -> str:
    """用 CSV 文本新建或整体覆盖一个 .xlsx（只写一个工作表）。"""
    if not isinstance(content, str):
        return "[错误] content 必须是字符串。"
    sheet_error = _validate_sheet_name(sheet_name)
    if sheet_error:
        return sheet_error
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_write_target(target, ".xlsx")
    if error:
        return error
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return f"[错误] 内容过大，上限 {_MAX_WRITE_BYTES} 字节"

    try:
        rows = [row for row in csv.reader(io.StringIO(content)) if any(row)]
    except csv.Error as exc:
        return f"[错误] CSV 解析失败: {exc}"
    if not rows:
        return "[错误] content 为空，无法生成 Excel"

    try:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        for row in rows:
            sheet.append([_coerce_excel_value(cell) for cell in row])
        buffer = io.BytesIO()
        workbook.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 Excel 失败: {exc}"

    return _write_office_target(target, buffer.getvalue())


@tool
def edit_xlsx(path: str, sheet: str, cell: str, value: str) -> str:
    """修改 .xlsx 中某个工作表的单个单元格；保留其他工作表和样式。"""
    if not isinstance(sheet, str) or not sheet:
        return "[错误] sheet 不能为空。"
    if not isinstance(cell, str) or not cell:
        return "[错误] cell 不能为空。"
    if not isinstance(value, str):
        return "[错误] value 必须是字符串。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".xlsx")
    if error:
        return error

    normalized = cell.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}[1-9]\d*", normalized):
        return f"[错误] cell 必须是 A1 形式: {cell!r}"

    try:
        workbook = load_workbook(str(target), data_only=False)
    except Exception as exc:
        return f"[错误] 打开 Excel 失败: {target} ({exc})"

    if sheet not in workbook.sheetnames:
        return f"[错误] 工作表不存在: {sheet}"

    worksheet = workbook[sheet]
    worksheet[normalized] = _coerce_excel_value(value)

    try:
        buffer = io.BytesIO()
        workbook.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 Excel 失败: {target} ({exc})"

    result = _write_office_target(target, buffer.getvalue())
    result = result.replace("[写入]", "[修改]")
    return result


def _pptx_text_shapes(slide: Any) -> Iterator[Any]:
    """Yield text shapes and table-cell paragraphs of a slide (title excluded)."""
    title = slide.shapes.title
    for shape in slide.shapes:
        if shape is title:
            continue
        if getattr(shape, "has_text_frame", False):
            yield shape
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    for paragraph in cell.text_frame.paragraphs:
                        yield paragraph


def _replace_in_pptx_paragraph(
    paragraph: Any,
    old_text: str,
    new_text: str,
    replace_all: bool,
) -> tuple[int, bool]:
    """Replace text inside one paragraph; returns (count, rebuilt_flag)."""
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
            run._r.getparent().remove(run._r)
        first.text = updated
    else:
        paragraph.text = updated
    return count if replace_all else 1, True


@tool
def read_pptx(path: str, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """读取 .pptx 的幻灯片标题、文本框和表格文字。"""
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".pptx")
    if error:
        return error

    limit = _coerce_max_chars(max_chars)
    try:
        presentation = Presentation(str(target))
    except Exception as exc:
        return f"[错误] 打开 PPT 失败: {target} ({exc})"

    lines = [f"[演示文稿] {target}"]
    for slide_index, slide in enumerate(presentation.slides, 1):
        header = f"[幻灯片 {slide_index}]"
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.has_text_frame:
            title = title_shape.text_frame.text.strip()
            if title:
                header += f" 标题: {title}"
        lines.append(header)
        for shape in slide.shapes:
            if shape is title_shape:
                continue
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(f"  文本框: {text.replace(chr(10), ' / ')}")
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
                    lines.append("  表格行: " + " | ".join(cells))
    return _truncate("\n".join(lines), limit)


@tool
def write_pptx(path: str, content: str) -> str:
    """用 Markdown 新建或整体覆盖 .pptx；用单独一行 --- 分页。"""
    if not isinstance(content, str):
        return "[错误] content 必须是字符串。"
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_write_target(target, ".pptx")
    if error:
        return error
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return f"[错误] 内容过大，上限 {_MAX_WRITE_BYTES} 字节"

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip() == "---":
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    blocks = [block for block in blocks if any(line.strip() for line in block)]
    if not blocks:
        return "[错误] content 为空，无法生成 PPT"

    try:
        presentation = Presentation()
        for block in blocks:
            title = ""
            body = block
            if block and block[0].strip().startswith("# "):
                title = block[0].strip()[2:].strip()
                body = block[1:]
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = title
            text_frame = slide.placeholders[1].text_frame
            text_frame.clear()
            first = True
            for line in body:
                text = line.strip()
                if not text:
                    continue
                paragraph = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
                first = False
                paragraph.text = text[2:].strip() if text.startswith("- ") else text
        buffer = io.BytesIO()
        presentation.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 PPT 失败: {exc}"

    return _write_office_target(target, buffer.getvalue())


@tool
def edit_pptx(
    path: str,
    slide: int,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """在 .pptx 指定页（从 1 开始）替换文字。"""
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "[错误] old_text 和 new_text 必须是字符串。"
    if not old_text:
        return "[错误] old_text 不能为空。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".pptx")
    if error:
        return error

    try:
        slide_number = int(slide)
    except (TypeError, ValueError):
        return f"[错误] slide 必须是整数: {slide!r}"

    try:
        presentation = Presentation(str(target))
    except Exception as exc:
        return f"[错误] 打开 PPT 失败: {target} ({exc})"

    if slide_number < 1 or slide_number > len(presentation.slides):
        return f"[错误] 幻灯片页码越界: {slide_number}"

    slide_obj = presentation.slides[slide_number - 1]
    paragraphs = []
    title_shape = slide_obj.shapes.title
    if title_shape is not None and title_shape.has_text_frame:
        paragraphs.extend(title_shape.text_frame.paragraphs)
    for shape in slide_obj.shapes:
        if shape is title_shape:
            continue
        if getattr(shape, "has_text_frame", False):
            paragraphs.extend(shape.text_frame.paragraphs)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    paragraphs.extend(cell.text_frame.paragraphs)

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
        count, rebuilt = _replace_in_pptx_paragraph(
            paragraph, old_text, new_text, replace_all
        )
        replaced += count
        warnings += int(rebuilt)

    try:
        buffer = io.BytesIO()
        presentation.save(buffer)
    except Exception as exc:
        return f"[错误] 生成 PPT 失败: {target} ({exc})"

    result = _write_office_target(target, buffer.getvalue())
    result = result.replace("[写入]", "[修改]")
    if warnings:
        result += f"\n[提示] {warnings} 处跨格式片段已按段落样式重建"
    return result


@tool
def read_pdf(
    path: str,
    max_pages: int = 50,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """只读提取 .pdf 的文字；不做 OCR，不支持修改 PDF。"""
    try:
        target = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"
    error = _validate_existing(target, ".pdf")
    if error:
        return error

    page_limit = _coerce_limit(max_pages, 50, _MAX_PDF_PAGES)
    char_limit = _coerce_max_chars(max_chars)

    try:
        reader = PdfReader(str(target))
    except Exception as exc:
        return f"[错误] 打开 PDF 失败: {target} ({exc})"

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            return f"[错误] PDF 已加密，无法读取: {target}"

    lines = [f"[PDF] {target}"]
    page_count = min(len(reader.pages), page_limit)
    for page_index in range(1, page_count + 1):
        try:
            text = reader.pages[page_index - 1].extract_text() or ""
        except Exception as exc:
            lines.append(f"[第 {page_index} 页] [错误] 提取失败: {exc}")
            continue
        text = text.strip()
        if text:
            lines.append(f"[第 {page_index} 页]\n{text}")

    if len(lines) == 1:
        return "[提示] 未提取到文字，可能是扫描版 PDF"
    if len(reader.pages) > page_limit:
        lines.append(f"...[仅显示前 {page_limit} 页]")
    return _truncate("\n".join(lines), char_limit)


OFFICE_TOOLS = [
    read_docx,
    write_docx,
    edit_docx,
    read_xlsx,
    write_xlsx,
    edit_xlsx,
    read_pptx,
    write_pptx,
    edit_pptx,
    read_pdf,
]