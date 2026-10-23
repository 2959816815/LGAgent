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


if __name__ == "__main__":
    unittest.main()
