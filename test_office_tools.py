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
