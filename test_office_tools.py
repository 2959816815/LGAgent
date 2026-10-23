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


from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from office_tools import edit_xlsx, read_xlsx, write_xlsx


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


from pptx import Presentation
from pptx.util import Pt

from office_tools import edit_pptx, read_pptx, write_pptx


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
        paragraph.font.size = Pt(24)
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


from office_tools import read_pdf


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

    def test_read_pdf_rejects_wrong_extension(self) -> None:
        target = self.root / "notes.docx"
        target.write_bytes(b"x")
        result = read_pdf.invoke({"path": str(target)})
        self.assertIn("[错误]", result)
        self.assertIn(".pdf", result)


if __name__ == "__main__":
    unittest.main()
