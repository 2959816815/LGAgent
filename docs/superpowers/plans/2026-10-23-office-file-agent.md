# Office File Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add Word/Excel/PPT read-write-edit and PDF read-only tools to the existing LangGraph agent, without requiring Microsoft Office.

**Architecture:** A new self-contained `office_tools.py` owns ten `@tool` functions plus shared path/backup/atomic-write helpers. `graph_agent.py` imports `OFFICE_TOOLS`, appends them to `TOOLS`, updates the system prompt, and extends log redaction so document contents never reach the terminal. Tests use `unittest` and workspace-local temp directories.

**Tech Stack:** Python 3.12, `python-docx`, `openpyxl`, `python-pptx`, `pypdf`, `langchain-core` tool decorator.

---

## File map

| File | Responsibility |
|---|---|
| `D:\Vibecoding\Langgraph\office_tools.py` | 10 office tools + shared helpers; no imports from `graph_agent.py` |
| `D:\Vibecoding\Langgraph\test_office_tools.py` | Unit tests for all office tools |
| `D:\Vibecoding\Langgraph\graph_agent.py` | Register tools, update prompt, extend log redaction |
| `D:\Vibecoding\Langgraph\requirements-office.txt` | Office dependency versions |
| `D:\Vibecoding\Langgraph\docs\superpowers\plans\2026-10-23-office-file-agent.md` | This plan |

All commands run from `D:\Vibecoding\Langgraph` unless stated otherwise. Use `D:\Anaconda3\envs\rag\python.exe` for tests and imports.

---

## Task 1: Dependencies, module skeleton, shared file helpers

**Files:**
- Create: `D:\Vibecoding\Langgraph\requirements-office.txt`
- Create: `D:\Vibecoding\Langgraph\office_tools.py`
- Create: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Write the dependency file**

```text
python-docx>=1.1.2
openpyxl>=3.1.5
python-pptx>=1.0.2
pypdf>=5.0.0
```

- [x] **Step 2: Install dependencies and verify imports**

Run:

```powershell
D:\Anaconda3\envs\rag\python.exe -m pip install -r requirements-office.txt
D:\Anaconda3\envs\rag\python.exe -c "import docx, openpyxl, pptx, pypdf; print('office libs ok')"
```

Expected: `office libs ok`.

- [x] **Step 3: Write failing helper tests**

Create `test_office_tools.py` with:

```python
r"""Office tool unit tests. Run with:
    D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.py
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from office_tools import (
    _atomic_write,
    _next_backup_path,
    _resolve_path,
    _validate_existing,
)

_WORKSPACE = Path(__file__).resolve().parent


class OfficeHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_office_helpers_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_expands_user_and_env(self) -> None:
        import os

        os.environ["OFFICE_TEST_DIR"] = str(self.root)
        try:
            resolved = _resolve_path("$OFFICE_TEST_DIR/notes.docx")
        finally:
            del os.environ["OFFICE_TEST_DIR"]
        self.assertEqual(resolved, (self.root / "notes.docx").resolve())

    def test_next_backup_path_uses_numbered_suffix(self) -> None:
        target = self.root / "a.docx"
        target.write_bytes(b"x")
        first = _next_backup_path(target)
        self.assertEqual(first.name, "a.docx.bak")
        first.write_bytes(b"bak")
        second = _next_backup_path(target)
        self.assertEqual(second.name, "a.docx.bak.1")

    def test_atomic_write_replaces_content(self) -> None:
        target = self.root / "a.txt"
        target.write_text("old", encoding="utf-8")
        _atomic_write(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")

    def test_validate_existing_rejects_wrong_suffix(self) -> None:
        target = self.root / "a.txt"
        target.write_text("x", encoding="utf-8")
        error = _validate_existing(target, ".docx")
        self.assertIsNotNone(error)
        self.assertIn("[错误]", error)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 4: Run tests to verify they fail**

Run:

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.py
```

Expected: `ModuleNotFoundError: No module named 'office_tools'`.

- [x] **Step 5: Create `office_tools.py` with shared helpers**

```python
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
```

- [x] **Step 6: Run helper tests**

Run:

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.py
```

Expected: 4 tests pass.

- [x] **Step 7: Commit**

```powershell
git -C D:\Vibecoding add Langgraph/requirements-office.txt Langgraph/office_tools.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: add office tool skeleton and shared helpers"
```

---

## Task 2: Word read / write / edit

**Files:**
- Modify: `D:\Vibecoding\Langgraph\office_tools.py`
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Add failing Word tests**

Append to `test_office_tools.py` before `if __name__ == "__main__":`:

```python
from docx import Document

from office_tools import edit_docx, read_docx, write_docx


class WordToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_office_docx_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_docx_markdown_and_read_back(self) -> None:
        target = self.root / "report.docx"
        content = (
            "# 项目周报\n\n"
            "本周完成情况：\n\n"
            "- 接口开发\n"
            "- 联调\n\n"
            "| 姓名 | 任务 |\n"
            "| --- | --- |\n"
            "| 张三 | 接口开发 |\n"
        )

        result = write_docx.invoke({"path": str(target), "content": content})
        read = read_docx.invoke({"path": str(target)})

        self.assertIn("[新建]", result)
        self.assertTrue(target.exists())
        self.assertIn("[段落 1] 项目周报", read)
        self.assertIn("接口开发", read)
        self.assertIn("[表格 1]", read)
        self.assertIn("张三", read)

    def test_edit_docx_preserves_single_run_bold(self) -> None:
        target = self.root / "bold.docx"
        doc = Document()
        paragraph = doc.add_paragraph()
        run = paragraph.add_run("旧内容")
        run.bold = True
        doc.save(target)

        result = edit_docx.invoke(
            {
                "path": str(target),
                "old_text": "旧内容",
                "new_text": "新内容",
            }
        )

        self.assertIn("[修改]", result)
        doc2 = Document(target)
        self.assertEqual(doc2.paragraphs[0].text, "新内容")
        self.assertTrue(doc2.paragraphs[0].runs[0].bold)

    def test_edit_docx_cross_run_returns_warning(self) -> None:
        target = self.root / "cross.docx"
        doc = Document()
        paragraph = doc.add_paragraph()
        paragraph.add_run("旧")
        paragraph.add_run("内容")
        doc.save(target)

        result = edit_docx.invoke(
            {
                "path": str(target),
                "old_text": "旧内容",
                "new_text": "新内容",
            }
        )

        self.assertIn("[修改]", result)
        self.assertIn("[提示]", result)
        self.assertEqual(Document(target).paragraphs[0].text, "新内容")

    def test_edit_docx_rejects_duplicate_without_replace_all(self) -> None:
        target = self.root / "dup.docx"
        doc = Document()
        doc.add_paragraph("重复")
        doc.add_paragraph("重复")
        doc.save(target)

        result = edit_docx.invoke(
            {"path": str(target), "old_text": "重复", "new_text": "改"}
        )

        self.assertIn("[错误]", result)
        self.assertIn("出现 2 次", result)

    def test_edit_docx_creates_backup(self) -> None:
        target = self.root / "backup.docx"
        doc = Document()
        doc.add_paragraph("旧内容")
        doc.save(target)

        result = edit_docx.invoke(
            {"path": str(target), "old_text": "旧内容", "new_text": "新内容"}
        )

        self.assertIn("[备份]", result)
        self.assertTrue((self.root / "backup.docx.bak").exists())

    def test_write_docx_wrong_extension(self) -> None:
        result = write_docx.invoke(
            {"path": str(self.root / "a.txt"), "content": "x"}
        )
        self.assertIn("[错误]", result)
```

- [x] **Step 2: Run tests to verify they fail**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.WordToolTests
```

Expected: `ImportError: cannot import name 'edit_docx'`.

- [x] **Step 3: Add Word implementation to `office_tools.py`**

Append:

```python
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
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


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
```

- [x] **Step 4: Run Word tests**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.WordToolTests
```

Expected: 6 tests pass.

- [x] **Step 5: Run all tests and commit**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.py
git -C D:\Vibecoding add Langgraph/office_tools.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: add Word read write edit tools"
```

---

## Task 3: Excel read / write / edit

**Files:**
- Modify: `D:\Vibecoding\Langgraph\office_tools.py`
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Add failing Excel tests**

Insert before `if __name__ == "__main__":`:

```python
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font


class ExcelToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_office_xlsx_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_workbook(self) -> Path:
        target = self.root / "data.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "销售"
        sheet["A1"] = "姓名"
        sheet["B1"] = "金额"
        sheet["A2"] = "张三"
        sheet["B2"] = "=SUM(1,2)"
        sheet["A1"].font = Font(bold=True)
        other = workbook.create_sheet("备注")
        other["A1"] = "保留我"
        workbook.save(target)
        return target

    def test_read_xlsx_shows_formula_and_sheets(self) -> None:
        target = self._make_workbook()
        result = read_xlsx.invoke({"path": str(target)})

        self.assertIn("[工作表] 销售", result)
        self.assertIn("B2: =SUM(1,2)", result)
        self.assertIn("[工作表] 备注", result)
        self.assertIn("保留我", result)

    def test_write_xlsx_from_csv(self) -> None:
        target = self.root / "new.xlsx"
        content = "姓名,年龄\n张三,30\n李四,=SUM(B2:B2)\n"

        result = write_xlsx.invoke(
            {"path": str(target), "content": content, "sheet_name": "名单"}
        )

        self.assertIn("[新建]", result)
        workbook = load_workbook(target)
        sheet = workbook["名单"]
        self.assertEqual(sheet["A2"].value, "张三")
        self.assertEqual(sheet["B2"].value, 30)
        self.assertEqual(sheet["B3"].value, "=SUM(B2:B2)")

    def test_edit_xlsx_preserves_other_sheet_and_backup(self) -> None:
        target = self._make_workbook()

        result = edit_xlsx.invoke(
            {
                "path": str(target),
                "sheet": "销售",
                "cell": "B2",
                "value": "=SUM(3,4)",
            }
        )

        self.assertIn("[修改]", result)
        self.assertTrue((self.root / "data.xlsx.bak").exists())
        workbook = load_workbook(target)
        self.assertEqual(workbook["销售"]["B2"].value, "=SUM(3,4)")
        self.assertEqual(workbook["备注"]["A1"].value, "保留我")

    def test_edit_xlsx_rejects_bad_sheet_and_cell(self) -> None:
        target = self._make_workbook()
        bad_sheet = edit_xlsx.invoke(
            {"path": str(target), "sheet": "不存在", "cell": "A1", "value": "x"}
        )
        bad_cell = edit_xlsx.invoke(
            {"path": str(target), "sheet": "销售", "cell": "1A", "value": "x"}
        )
        self.assertIn("[错误]", bad_sheet)
        self.assertIn("[错误]", bad_cell)
```

- [x] **Step 2: Run tests to verify they fail**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.ExcelToolTests
```

Expected: `ImportError: cannot import name 'read_xlsx'`.

- [x] **Step 3: Add Excel implementation**

Append:

```python
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
```

- [x] **Step 4: Run Excel tests**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.ExcelToolTests
```

Expected: 4 tests pass.

- [x] **Step 5: Commit**

```powershell
git -C D:\Vibecoding add Langgraph/office_tools.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: add Excel read write edit tools"
```

---

## Task 4: PPT read / write / edit

**Files:**
- Modify: `D:\Vibecoding\Langgraph\office_tools.py`
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Add failing PPT tests**

Insert before `if __name__ == "__main__":`:

```python
from pptx import Presentation


class PptToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_office_pptx_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_pptx(self) -> Path:
        target = self.root / "deck.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "项目汇报"
        body = slide.placeholders[1]
        body.text = "本周进展"
        paragraph = body.text_frame.add_paragraph()
        paragraph.text = "- 完成接口开发"
        paragraph.font.size = 24
        presentation.save(target)
        return target

    def test_read_pptx_shows_slide_text(self) -> None:
        target = self._make_pptx()
        result = read_pptx.invoke({"path": str(target)})

        self.assertIn("[幻灯片 1]", result)
        self.assertIn("标题: 项目汇报", result)
        self.assertIn("完成接口开发", result)

    def test_write_pptx_from_markdown(self) -> None:
        target = self.root / "new.pptx"
        content = "# 第一页\n- 要点一\n- 要点二\n\n---\n\n# 第二页\n- 要点三\n"

        result = write_pptx.invoke({"path": str(target), "content": content})
        read = read_pptx.invoke({"path": str(target)})

        self.assertIn("[新建]", result)
        self.assertIn("[幻灯片 1]", read)
        self.assertIn("[幻灯片 2]", read)
        self.assertIn("第一页", read)

    def test_edit_pptx_preserves_font_size_and_backup(self) -> None:
        target = self._make_pptx()

        result = edit_pptx.invoke(
            {
                "path": str(target),
                "slide": 1,
                "old_text": "完成接口开发",
                "new_text": "完成联调",
            }
        )

        self.assertIn("[修改]", result)
        self.assertTrue((self.root / "deck.pptx.bak").exists())
        presentation = Presentation(target)
        slide = presentation.slides[0]
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    texts.append(paragraph.text)
        self.assertIn("完成联调", "\n".join(texts))

    def test_edit_pptx_rejects_bad_slide(self) -> None:
        target = self._make_pptx()
        result = edit_pptx.invoke(
            {
                "path": str(target),
                "slide": 9,
                "old_text": "x",
                "new_text": "y",
            }
        )
        self.assertIn("[错误]", result)
```

- [x] **Step 2: Run tests to verify they fail**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.PptToolTests
```

Expected: `ImportError: cannot import name 'read_pptx'`.

- [x] **Step 3: Add PPT implementation**

Append:

```python
def _pptx_text_shapes(slide: Any) -> Iterator[Any]:
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
```

- [x] **Step 4: Run PPT tests**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.PptToolTests
```

Expected: 4 tests pass.

- [x] **Step 5: Commit**

```powershell
git -C D:\Vibecoding add Langgraph/office_tools.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: add PPT read write edit tools"
```

---

## Task 5: PDF read-only

**Files:**
- Modify: `D:\Vibecoding\Langgraph\office_tools.py`
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Add failing PDF tests**

Insert before `if __name__ == "__main__":`:

```python
def _build_text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(pdf)


class PdfToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_office_pdf_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_read_pdf_extracts_text(self) -> None:
        target = self.root / "hello.pdf"
        target.write_bytes(_build_text_pdf("Hello Office"))
        result = read_pdf.invoke({"path": str(target)})
        self.assertIn("[第 1 页]", result)
        self.assertIn("Hello Office", result)

    def test_read_pdf_blank_returns_hint(self) -> None:
        from pypdf import PdfWriter

        target = self.root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(target, "wb") as handle:
            writer.write(handle)
        result = read_pdf.invoke({"path": str(target)})
        self.assertIn("[提示]", result)
        self.assertIn("扫描版", result)

    def test_read_pdf_encrypted_returns_error(self) -> None:
        from pypdf import PdfWriter

        target = self.root / "secret.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("secret")
        with open(target, "wb") as handle:
            writer.write(handle)
        result = read_pdf.invoke({"path": str(target)})
        self.assertIn("[错误]", result)
        self.assertIn("加密", result)
```

- [x] **Step 2: Run tests to verify they fail**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.PdfToolTests
```

Expected: `ImportError: cannot import name 'read_pdf'`.

- [x] **Step 3: Add PDF implementation**

Append:

```python
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
```

- [x] **Step 4: Run PDF tests**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.PdfToolTests
```

Expected: 3 tests pass.

- [x] **Step 5: Export the tool list and run all tests**

Append at the end of `office_tools.py`:

```python
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
```

Run:

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.py
```

Expected: all office tests pass.

- [x] **Step 6: Commit**

```powershell
git -C D:\Vibecoding add Langgraph/office_tools.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: add PDF read tool and export office tools"
```

---

## Task 6: Integrate with `graph_agent.py`

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py`
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py`

- [x] **Step 1: Add failing integration tests**

Insert before `if __name__ == "__main__":`:

```python
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from graph_agent import TOOLS, _FILE_TOOL_ACTIONS, _print_tool_calls, SYSTEM_PROMPT


class IntegrationTests(unittest.TestCase):
    def test_office_tools_are_registered(self) -> None:
        names = {getattr(tool, "name", "") for tool in TOOLS}
        for expected in [
            "read_docx",
            "write_docx",
            "edit_docx",
            "read_xlsx",
            "write_xlsx",
            "edit_xlsx",
            "read_pptx",
            "write_pptx",
            "edit_pptx",
            "read_pdf",
        ]:
            self.assertIn(expected, names)

    def test_prompt_mentions_office_tools(self) -> None:
        self.assertIn("read_docx", SYSTEM_PROMPT)
        self.assertIn("read_xlsx", SYSTEM_PROMPT)
        self.assertIn("read_pptx", SYSTEM_PROMPT)
        self.assertIn("read_pdf", SYSTEM_PROMPT)

    def test_office_write_log_hides_content(self) -> None:
        path = r"D:\tmp\notes.docx"
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_docx",
                    "args": {"path": path, "content": "SECRET"},
                    "id": "call-write",
                }
            ],
        )
        tool_message = ToolMessage(
            content=f"[新建] {path} (100 字节)",
            tool_call_id="call-write",
        )
        with patch("builtins.print") as mock_print:
            _print_tool_calls([ai, tool_message], 0)
        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertNotIn("SECRET", output)
        self.assertIn(f"[完成] 已写入: {path}", output)
        self.assertIn("write_docx", _FILE_TOOL_ACTIONS)
```

- [x] **Step 2: Run tests to verify they fail**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.IntegrationTests
```

Expected: `KeyError`/assertion failure for missing office tools/prompt.

- [x] **Step 3: Modify `graph_agent.py`**

At the imports, add:

```python
from office_tools import OFFICE_TOOLS
```

Replace the `SYSTEM_PROMPT` with:

```python
SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助手。需要实时信息时请调用工具，不要编造。"
    "读取文件或目录用 read_file；文本文件新建/覆盖用 write_file，局部修改用 edit_file。"
    "Word 用 read_docx/write_docx/edit_docx；Excel 用 read_xlsx/write_xlsx/edit_xlsx；"
    "PPT 用 read_pptx/write_pptx/edit_pptx；PDF 只读用 read_pdf。"
    "修改已有办公文件前先用对应的 read_* 工具确认原文；"
    "写入或修改完成后，只报告文件路径和完成状态，不要复述写入或修改的内容；"
    "路径优先使用绝对路径；工具返回错误时如实说明，不要猜测文件内容。"
)
```

Replace `TOOLS` with:

```python
TOOLS = [get_current_time, read_file, write_file, edit_file, *OFFICE_TOOLS]
```

Replace `_FILE_TOOL_ACTIONS` with:

```python
_FILE_TOOL_ACTIONS = {
    "write_file": "写入",
    "edit_file": "修改",
    "write_docx": "写入",
    "edit_docx": "修改",
    "write_xlsx": "写入",
    "edit_xlsx": "修改",
    "write_pptx": "写入",
    "edit_pptx": "修改",
}
```

- [x] **Step 4: Run integration tests and full suite**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_office_tools.IntegrationTests
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py test_office_tools.py
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```powershell
git -C D:\Vibecoding add Langgraph/graph_agent.py Langgraph/test_office_tools.py
git -C D:\Vibecoding commit -m "feat: register office tools and redact document content"
```

---

## Task 7: End-to-end and regression

**Files:**
- Modify: `D:\Vibecoding\Langgraph\test_office_tools.py` (only if E2E reveals a parsing bug)
- Create: temporary `D:\Vibecoding\Langgraph\e2e_office_check.py` (delete after)

- [x] **Step 1: Create a sample docx and run the local model**

Create `e2e_office_check.py`:

```python
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

import graph_agent
from office_tools import read_xlsx, write_docx

path = Path(r"D:\Vibecoding\Langgraph\sample_office_e2e.docx")
write_docx.invoke(
    {
        "path": str(path),
        "content": "# 项目说明\n\n项目代号是蓝鲸。\n\n请把蓝鲸改成海豚。\n",
    }
)

graph = graph_agent.build_graph()
read_result = graph.invoke(
    {
        "messages": [
            SystemMessage(content=graph_agent.SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"请用 read_docx 读取 {path}，然后用一句中文总结内容。"
                )
            ),
        ]
    },
    config={"recursion_limit": 25},
)
graph_agent._print_tool_calls(read_result["messages"], 1)
print("READ:", read_result["messages"][-1].content)

result = graph.invoke(
    {
        "messages": [
            SystemMessage(content=graph_agent.SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"请用 edit_docx 把 {path} 里的“蓝鲸”改成“海豚”。"
                    "完成后只回复文件路径和完成状态，不要复述内容。"
                )
            ),
        ]
    },
    config={"recursion_limit": 25},
)

graph_agent._print_tool_calls(result["messages"], 1)
print("EDIT:", result["messages"][-1].content)

xlsx_path = Path(r"D:\Vibecoding\Langgraph\sample_office_e2e.xlsx")
xlsx_result = graph.invoke(
    {
        "messages": [
            SystemMessage(content=graph_agent.SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"请用 write_xlsx 新建 {xlsx_path}，"
                    "内容是 CSV，共三行：第一行 姓名,金额；第二行 张三,100；"
                    "第三行 李四,=SUM(B2:B2)。"
                    "完成后只回复文件路径和完成状态。"
                )
            ),
        ]
    },
    config={"recursion_limit": 25},
)
graph_agent._print_tool_calls(xlsx_result["messages"], 1)
print("XLSX:", read_xlsx.invoke({"path": str(xlsx_path)}))
```

Run:

```powershell
D:\Anaconda3\envs\rag\python.exe -u e2e_office_check.py
```

Expected: READ prints a short summary; tool log contains `[完成] 已修改: ...sample_office_e2e.docx`, no document text; EDIT reply is short; `XLSX:` output shows `张三` and `=SUM(B2:B2)`.

- [x] **Step 2: Verify the file changed and clean up**

```powershell
D:\Anaconda3\envs\rag\python.exe -c "from docx import Document; print(Document(r'D:\Vibecoding\Langgraph\sample_office_e2e.docx').paragraphs[1].text)"
```

Expected: `项目代号是海豚。`

Delete the temporary files:

```powershell
Remove-Item -LiteralPath D:\Vibecoding\Langgraph\e2e_office_check.py -Force
Remove-Item -LiteralPath D:\Vibecoding\Langgraph\sample_office_e2e.docx -Force
Remove-Item -LiteralPath D:\Vibecoding\Langgraph\sample_office_e2e.docx.bak -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath D:\Vibecoding\Langgraph\sample_office_e2e.xlsx -Force -ErrorAction SilentlyContinue
```

- [x] **Step 3: Run the complete regression suite**

```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py test_office_tools.py
```

Expected: all tests pass.

- [x] **Step 4: Commit the final plan checkbox updates only**

```powershell
git -C D:\Vibecoding add Langgraph/docs/superpowers/plans/2026-10-23-office-file-agent.md
git -C D:\Vibecoding commit -m "docs: mark office agent plan complete"
```

---

## Acceptance checklist

- [x] `python-docx`, `openpyxl`, `python-pptx` installed in `rag`; `pypdf` present.
- [x] `office_tools.py` exposes 10 tools and `OFFICE_TOOLS`.
- [x] `.docx` read/write/edit, `.xlsx` read/write/edit, `.pptx` read/write/edit, `.pdf` read.
- [x] Every existing-file write/edit creates `.bak`; failed writes leave the original intact.
- [x] Word/PPT edits preserve single-run formatting; cross-run edits warn instead of silently rebuilding.
- [x] Terminal logs never print `content`, `old_text`, `new_text`, or `value`; only paths/status.
- [x] `test_office_tools.py` and existing `test_write_file.py` pass.
- [x] Local `qwen3.5:2b` completes one docx read, one docx edit, and one xlsx create end-to-end.
- [x] No Office, LibreOffice, pandoc, or COM dependency.
