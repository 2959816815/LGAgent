"""File discovery, text extraction, and chunking for the knowledge base."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from pptx import Presentation

from context_builder import estimate_text_tokens

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".json",
}
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".cache",
    "target",
}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_XLSX_ROWS = 2000
MAX_XLSX_CHARS = 50_000
MAX_CSV_ROWS = 5000
CHUNK_SIZE = 600
CHUNK_OVERLAP = 100
MIN_CHUNK = 50
MAX_CHUNK = 1000


@dataclass(frozen=True)
class TextUnit:
    text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    embed_text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None
    tokens: int = 0


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(8192)
    except OSError:
        return True


def discover_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and name.lower() not in SKIP_DIRS
            and not (current / name).is_symlink()
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = current / name
            if path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            if path.suffix.lower() in {".txt", ".md", ".markdown", ".csv", ".json"}:
                if _looks_binary(path):
                    continue
            yield path


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return _decode(data)[:MAX_TEXT_CHARS]


def _extract_markdown(path: Path) -> list[TextUnit]:
    units: list[TextUnit] = []
    stack: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            units.append(TextUnit(text=text, heading=" / ".join(stack)))
        buffer.clear()

    for line in _read_text(path).splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            flush()
            level = len(match.group(1))
            stack[:] = stack[: level - 1]
            stack.append(match.group(2).strip())
        else:
            buffer.append(line)
    flush()
    return units


def _extract_docx(path: Path) -> list[TextUnit]:
    document = Document(str(path))
    units: list[TextUnit] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            units.append(TextUnit(text=text, heading=heading))
        buffer.clear()

    for paragraph in document.paragraphs:
        style = paragraph.style.name if paragraph.style else ""
        if style.startswith("Heading") or style.startswith("标题"):
            flush()
            heading = paragraph.text.strip()
            continue
        if paragraph.text.strip():
            buffer.append(paragraph.text)
    flush()

    for table in document.tables:
        rows = []
        for row in table.rows:
            rows.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = "\n".join(rows).strip()
        if text:
            units.append(TextUnit(text=text, heading=heading))
    return units


def _extract_xlsx(path: Path) -> list[TextUnit]:
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    units: list[TextUnit] = []
    try:
        for sheet in workbook.worksheets:
            lines: list[str] = []
            chars = 0
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_XLSX_ROWS or chars >= MAX_XLSX_CHARS:
                    break
                line = " | ".join("" if value is None else str(value) for value in row).strip()
                if line:
                    lines.append(line)
                    chars += len(line)
            text = "\n".join(lines).strip()
            if text:
                units.append(TextUnit(text=text, sheet=sheet.title))
    finally:
        workbook.close()
    return units


def _extract_pptx(path: Path) -> list[TextUnit]:
    presentation = Presentation(str(path))
    units: list[TextUnit] = []
    for index, slide in enumerate(presentation.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                texts.append(shape.text.strip())
        text = "\n".join(texts).strip()
        if text:
            units.append(TextUnit(text=text, slide=index))
    return units


def _extract_csv(path: Path) -> list[TextUnit]:
    text = _read_text(path)
    lines: list[str] = []
    for index, row in enumerate(csv.reader(text.splitlines())):
        if index >= MAX_CSV_ROWS:
            break
        lines.append(" | ".join(row))
    joined = "\n".join(lines).strip()
    return [TextUnit(text=joined)] if joined else []


def _extract_json(path: Path) -> list[TextUnit]:
    text = _read_text(path)
    try:
        data = json.loads(text)
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pass
    text = text[:MAX_TEXT_CHARS].strip()
    return [TextUnit(text=text)] if text else []


def _extract_pdf(path: Path) -> list[TextUnit]:
    reader = PdfReader(str(path))
    units: list[TextUnit] = []
    total = 0
    for index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        total += len(text)
        if text:
            units.append(TextUnit(text=text, page=index))
    if total < 20:
        return []
    return units


def extract_units(path: Path) -> list[TextUnit]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return _extract_markdown(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".xlsx":
        return _extract_xlsx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".csv":
        return _extract_csv(path)
    if suffix == ".json":
        return _extract_json(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    text = _read_text(path).strip()
    return [TextUnit(text=text)] if text else []


def _split_long(paragraph: str, limit: int) -> list[str]:
    if len(paragraph) <= limit:
        return [paragraph]
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > limit:
        window = remaining[: limit + 50]
        cut = max(
            window.rfind("。"),
            window.rfind("！"),
            window.rfind("？"),
            window.rfind("；"),
            window.rfind(". "),
            window.rfind("\n"),
        )
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def _embed_text(unit: TextUnit, chunk: str) -> str:
    parts: list[str] = []
    if unit.heading:
        parts.append(f"[标题] {unit.heading}")
    if unit.page is not None:
        parts.append(f"[第{unit.page}页]")
    if unit.sheet:
        parts.append(f"[工作表] {unit.sheet}")
    if unit.slide is not None:
        parts.append(f"[第{unit.slide}页幻灯片]")
    parts.append(chunk)
    return "\n".join(parts)


def chunk_units(
    units: list[TextUnit],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    min_chunk: int = MIN_CHUNK,
    max_chunk: int = MAX_CHUNK,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for unit in units:
        text = unit.text.strip()
        if not text:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        pieces = [
            piece
            for paragraph in paragraphs
            for piece in _split_long(paragraph, max_chunk)
        ]
        buffer = ""
        unit_chunks: list[str] = []
        for piece in pieces:
            if not buffer:
                buffer = piece
            elif len(buffer) + 2 + len(piece) <= chunk_size:
                buffer = f"{buffer}\n\n{piece}"
            else:
                unit_chunks.append(buffer)
                tail = buffer[-overlap:] if overlap > 0 else ""
                buffer = (tail + "\n" + piece).strip()
        if buffer:
            unit_chunks.append(buffer)
        for index, chunk in enumerate(unit_chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(chunk) < min_chunk and len(unit_chunks) > 1:
                continue
            embed_text = _embed_text(unit, chunk)
            drafts.append(
                ChunkDraft(
                    text=chunk[:max_chunk],
                    embed_text=embed_text,
                    heading=unit.heading,
                    page=unit.page,
                    sheet=unit.sheet,
                    slide=unit.slide,
                    tokens=estimate_text_tokens(embed_text),
                )
            )
    return drafts
