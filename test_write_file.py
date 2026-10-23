r"""write_file / edit_file 工具的单元测试（标准库 unittest，零第三方依赖）。

运行（本机建议用含 LangChain 的 rag 环境）：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py

测试临时目录放在工作区内，避免 DSH 沙箱拒绝系统临时目录。
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from graph_agent import _print_tool_calls, edit_file, write_file

_WORKSPACE = Path(__file__).resolve().parent


def call_write(path: Path | str, content: str, **kwargs) -> str:
    """调用 LangChain 工具对象，等价于模型发起一次 tool call。"""
    return write_file.invoke({"path": str(path), "content": content, **kwargs})


def call_edit(
    path: Path | str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """调用 edit_file 工具对象。"""
    return edit_file.invoke(
        {
            "path": str(path),
            "old_text": old_text,
            "new_text": new_text,
            "replace_all": replace_all,
        }
    )


class WriteFileTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不用 TemporaryDirectory：它在这种 DSH 沙箱下创建 0700 目录，
        # 随后在该目录里 mkstemp 会卡住。这里用默认权限的普通目录。
        self.root = _WORKSPACE / f".test_write_file_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_utf8_file(self) -> None:
        target = self.root / "new.txt"

        result = call_write(target, "hello 世界")

        self.assertIn("[新建]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello 世界")

    def test_overwrite_creates_backup(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("旧内容", encoding="utf-8")

        result = call_write(target, "新内容")

        self.assertIn("[写入]", result)
        self.assertIn("[备份]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "新内容")
        backup = self.root / "notes.txt.bak"
        self.assertEqual(backup.read_text(encoding="utf-8"), "旧内容")

    def test_backup_collision_uses_numbered_suffix(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("当前内容", encoding="utf-8")
        (self.root / "notes.txt.bak").write_text("更早内容", encoding="utf-8")

        result = call_write(target, "最新内容")

        self.assertIn("[备份]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "最新内容")
        self.assertEqual(
            (self.root / "notes.txt.bak").read_text(encoding="utf-8"),
            "更早内容",
        )
        self.assertEqual(
            (self.root / "notes.txt.bak.1").read_text(encoding="utf-8"),
            "当前内容",
        )

    def test_missing_parent_directory_is_rejected(self) -> None:
        target = self.root / "missing" / "demo.txt"

        result = call_write(target, "hello")

        self.assertIn("[错误]", result)
        self.assertIn("父目录不存在", result)
        self.assertFalse(target.parent.exists())

    def test_directory_target_is_rejected(self) -> None:
        target = self.root / "folder"
        target.mkdir()

        result = call_write(target, "hello")

        self.assertIn("[错误]", result)
        self.assertIn("目标是目录", result)
        self.assertTrue(target.is_dir())

    def test_nul_in_content_is_rejected(self) -> None:
        target = self.root / "nul-content.txt"

        result = call_write(target, "a\x00b")

        self.assertIn("[错误]", result)
        self.assertIn("NUL", result)
        self.assertFalse(target.exists())

    def test_oversized_content_is_rejected(self) -> None:
        target = self.root / "big.txt"

        result = call_write(target, "a" * 5_000_001)

        self.assertIn("[错误]", result)
        self.assertIn("内容过大", result)
        self.assertFalse(target.exists())

    def test_binary_target_is_not_overwritten(self) -> None:
        target = self.root / "blob.bin"
        target.write_bytes(b"abc\x00def")

        result = call_write(target, "text")

        self.assertIn("[错误]", result)
        self.assertIn("二进制", result)
        self.assertEqual(target.read_bytes(), b"abc\x00def")
        self.assertFalse((self.root / "blob.bin.bak").exists())

    def test_chinese_content_and_path_with_spaces(self) -> None:
        target = self.root / "中文 文件.txt"

        result = call_write(target, "第一行\n第二行")

        self.assertIn("[新建]", result)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "第一行\n第二行",
        )

    def test_edit_single_occurrence(self) -> None:
        target = self.root / "single.txt"
        target.write_text("one two three", encoding="utf-8")

        result = call_edit(target, "one", "1")

        self.assertIn("[修改]", result)
        self.assertIn("替换 1 处", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "1 two three")
        self.assertEqual(
            (self.root / "single.txt.bak").read_text(encoding="utf-8"),
            "one two three",
        )

    def test_edit_missing_old_text_does_not_write(self) -> None:
        target = self.root / "missing.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "zzz", "x")

        self.assertIn("[错误]", result)
        self.assertIn("未找到", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc")
        self.assertFalse((self.root / "missing.txt.bak").exists())

    def test_edit_multiple_without_replace_all_is_rejected(self) -> None:
        target = self.root / "multi.txt"
        target.write_text("a a", encoding="utf-8")

        result = call_edit(target, "a", "b")

        self.assertIn("[错误]", result)
        self.assertIn("出现 2 次", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "a a")
        self.assertFalse((self.root / "multi.txt.bak").exists())

    def test_edit_replace_all(self) -> None:
        target = self.root / "all.txt"
        target.write_text("a a", encoding="utf-8")

        result = call_edit(target, "a", "b", replace_all=True)

        self.assertIn("替换 2 处", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "b b")
        self.assertTrue((self.root / "all.txt.bak").exists())

    def test_edit_empty_old_text_is_rejected(self) -> None:
        target = self.root / "empty-old.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "", "x")

        self.assertIn("[错误]", result)
        self.assertIn("old_text 不能为空", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc")

    def test_edit_empty_new_text_deletes_fragment(self) -> None:
        target = self.root / "delete.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "b", "")

        self.assertIn("[修改]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "ac")

    def test_edit_preserves_gb18030(self) -> None:
        target = self.root / "gb.txt"
        target.write_bytes("中文内容".encode("gb18030"))

        result = call_edit(target, "中文", "英文")

        self.assertIn("编码 gb18030", result)
        self.assertEqual(
            target.read_bytes().decode("gb18030"),
            "英文内容",
        )
        self.assertEqual(
            (self.root / "gb.txt.bak").read_bytes().decode("gb18030"),
            "中文内容",
        )

    def test_edit_preserves_utf8_bom(self) -> None:
        target = self.root / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbf" + "你好".encode("utf-8"))

        result = call_edit(target, "你好", "您好")

        self.assertIn("[修改]", result)
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.decode("utf-8-sig"), "您好")

    def test_edit_same_text_does_not_write(self) -> None:
        target = self.root / "same.txt"
        target.write_text("same", encoding="utf-8")
        before_mtime = target.stat().st_mtime_ns

        result = call_edit(target, "same", "same")

        self.assertIn("[提示]", result)
        self.assertEqual(target.stat().st_mtime_ns, before_mtime)
        self.assertFalse((self.root / "same.txt.bak").exists())

    def test_edit_creates_backup(self) -> None:
        target = self.root / "backup.txt"
        target.write_text("旧内容", encoding="utf-8")

        result = call_edit(target, "旧内容", "新内容")

        self.assertIn("[备份]", result)
        self.assertEqual(
            (self.root / "backup.txt.bak").read_text(encoding="utf-8"),
            "旧内容",
        )

    def test_edit_unencodable_new_text_does_not_write(self) -> None:
        target = self.root / "unencodable.txt"
        target.write_bytes("中文".encode("gb18030"))
        before = target.read_bytes()

        result = call_edit(target, "中文", "\ud800")

        self.assertIn("[错误]", result)
        self.assertIn("原编码", result)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.root / "unencodable.txt.bak").exists())

    def test_create_parent_directories_when_enabled(self) -> None:
        target = self.root / "nested" / "deep" / "note.txt"

        result = call_write(target, "hello", create_parents=True)

        self.assertIn("[新建]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_create_parents_refuses_file_parent(self) -> None:
        blocker = self.root / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        target = blocker / "note.txt"

        result = call_write(target, "hello", create_parents=True)

        self.assertIn("[错误]", result)
        self.assertFalse(target.exists())

    def test_write_gb18030_encoding(self) -> None:
        target = self.root / "gb-output.txt"

        result = call_write(target, "中文内容", encoding="gb18030")

        self.assertIn("[新建]", result)
        self.assertIn("GB18030", result)
        self.assertEqual(target.read_bytes().decode("gb18030"), "中文内容")

    def test_write_utf8_sig_has_bom(self) -> None:
        target = self.root / "bom-output.txt"

        result = call_write(target, "你好", encoding="utf-8-sig")

        self.assertIn("[新建]", result)
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_unsupported_encoding_is_rejected(self) -> None:
        target = self.root / "bad-encoding.txt"

        result = call_write(target, "hello", encoding="utf-16")

        self.assertIn("[错误]", result)
        self.assertIn("不支持", result)
        self.assertFalse(target.exists())

    def test_file_tool_output_hides_content(self) -> None:
        path = r"D:\tmp\notes.txt"
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"path": path, "content": "SECRET_WRITE"},
                    "id": "call-write",
                },
                {
                    "name": "edit_file",
                    "args": {
                        "path": path,
                        "old_text": "SECRET_OLD",
                        "new_text": "SECRET_NEW",
                    },
                    "id": "call-edit",
                },
            ],
        )
        tool_messages = [
            ToolMessage(
                content=f"[新建] {path} (6 字节, UTF-8)",
                tool_call_id="call-write",
            ),
            ToolMessage(
                content=f"[修改] {path} (替换 1 处, 编码 utf-8)",
                tool_call_id="call-edit",
            ),
        ]

        with patch("builtins.print") as mock_print:
            _print_tool_calls([ai, *tool_messages], 0)

        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertNotIn("SECRET_WRITE", output)
        self.assertNotIn("SECRET_OLD", output)
        self.assertNotIn("SECRET_NEW", output)
        self.assertIn(f"[完成] 已写入: {path}", output)
        self.assertIn(f"[完成] 已修改: {path}", output)


if __name__ == "__main__":
    unittest.main()
