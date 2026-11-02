from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from knowledge_extract import (
    ChunkDraft,
    TextUnit,
    chunk_units,
    discover_files,
    extract_units,
)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_discover_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_supported_files_are_found_and_noise_is_skipped(self):
        (self.root / "笔记.md").write_text("内容", encoding="utf-8")
        (self.root / "data.bin").write_text("内容", encoding="utf-8")
        (self.root / ".hidden.md").write_text("内容", encoding="utf-8")
        modules = self.root / "node_modules"
        modules.mkdir()
        (modules / "skip.md").write_text("内容", encoding="utf-8")

        names = {path.name for path in discover_files(self.root)}

        self.assertEqual(names, {"笔记.md"})

    def test_oversized_files_are_skipped(self):
        big = self.root / "big.txt"
        big.write_text("x" * 100, encoding="utf-8")
        with patch("knowledge_extract.MAX_FILE_BYTES", 10):
            names = {path.name for path in discover_files(self.root)}
        self.assertEqual(names, set())


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kb_extract_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_markdown_produces_heading_units(self):
        path = self.root / "a.md"
        path.write_text("# 标题一\n正文一\n\n## 标题二\n正文二", encoding="utf-8")

        units = extract_units(path)

        self.assertEqual(len(units), 2)
        self.assertIn("标题一", units[0].heading)

    def test_docx_and_xlsx_and_pptx_are_extracted(self):
        docx_path = self.root / "a.docx"
        document = Document()
        document.add_paragraph("段落内容")
        document.save(docx_path)
        self.assertTrue(any("段落内容" in unit.text for unit in extract_units(docx_path)))

        xlsx_path = self.root / "a.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "数据"
        sheet.append(["姓名", "部门"])
        workbook.save(xlsx_path)
        units = extract_units(xlsx_path)
        self.assertTrue(any(unit.sheet == "数据" for unit in units))

        pptx_path = self.root / "a.pptx"
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = "幻灯片标题"
        presentation.save(pptx_path)
        units = extract_units(pptx_path)
        self.assertTrue(any("幻灯片标题" in unit.text for unit in units))

    def test_pdf_uses_page_units(self):
        class FakePage:
            def extract_text(self):
                return "PDF 内容" * 10

        class FakeReader:
            def __init__(self, path):
                self.pages = [FakePage()]

        with patch("knowledge_extract.PdfReader", FakeReader):
            units = extract_units(self.root / "a.pdf")

        self.assertEqual(units[0].page, 1)
        self.assertIn("PDF 内容", units[0].text)


class ChunkingTests(unittest.TestCase):
    def test_long_text_is_split_around_the_target_size(self):
        unit = TextUnit(text="段落。" * 500, heading="标题")
        chunks = chunk_units([unit], chunk_size=100, overlap=20, max_chunk=200)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 240 for chunk in chunks))
        self.assertIn("[标题]", "".join(chunk.embed_text for chunk in chunks))

    def test_metadata_is_carried_into_every_chunk(self):
        unit = TextUnit(text="内容" * 100, heading="架构", page=3)
        chunks = chunk_units([unit], chunk_size=100, overlap=20)

        self.assertTrue(all(chunk.heading == "架构" for chunk in chunks))
        self.assertTrue(all(chunk.page == 3 for chunk in chunks))
        self.assertTrue(all("[第3页]" in chunk.embed_text for chunk in chunks))

    def test_short_file_is_kept(self):
        chunks = chunk_units([TextUnit(text="很短")], chunk_size=600)
        self.assertEqual(len(chunks), 1)
