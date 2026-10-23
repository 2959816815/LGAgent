r"""read_file 工具的单元测试（标准库 unittest，零第三方依赖）。

运行（本机建议用含 LangChain 的 rag 环境）：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v test_read_file.py
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from graph_agent import read_file

# 测试临时目录放在工作区内，避免 DSH 沙箱拒绝系统临时目录
_WORKSPACE = Path(__file__).resolve().parent


def call_read_file(path: Path | str, **kwargs) -> str:
    """调用 LangChain 工具对象，等价于模型发起一次 tool call。"""
    return read_file.invoke({"path": str(path), **kwargs})


class ReadFileTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不用 TemporaryDirectory：它在这种 DSH 沙箱下创建 0700 目录，
        # 随后在该目录里写入会失败或卡住。这里用默认权限的普通目录。
        self.root = _WORKSPACE / f".test_read_file_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_read_utf8_text(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("hello 世界\n第二行", encoding="utf-8")

        result = call_read_file(target)

        self.assertIn("hello 世界", result)
        self.assertIn("第二行", result)
        self.assertIn("[文件]", result)

    def test_list_directory(self) -> None:
        (self.root / "sub").mkdir()
        (self.root / "a.txt").write_text("a", encoding="utf-8")

        result = call_read_file(self.root)

        self.assertIn("[目录]", result)
        self.assertIn("[DIR]", result)
        self.assertIn("sub/", result)
        self.assertIn("[FILE]", result)
        self.assertIn("a.txt", result)

    def test_missing_file(self) -> None:
        result = call_read_file(self.root / "nope.txt")

        self.assertIn("[错误]", result)
        self.assertIn("不存在", result)

    def test_binary_rejected(self) -> None:
        target = self.root / "blob.bin"
        target.write_bytes(b"abc\x00def")

        result = call_read_file(target)

        self.assertIn("[错误]", result)
        self.assertIn("二进制", result)

    def test_truncation(self) -> None:
        target = self.root / "long.txt"
        target.write_text("a" * 1500, encoding="utf-8")

        result = call_read_file(target, max_chars=1000)

        self.assertIn("已截断", result)
        content = result.split("\n", 1)[1]
        self.assertEqual(content.count("a"), 1000)

    def test_gb18030_encoding(self) -> None:
        target = self.root / "gbk.txt"
        target.write_bytes("中文内容".encode("gb18030"))

        result = call_read_file(target)

        self.assertIn("中文内容", result)

    def test_path_with_spaces(self) -> None:
        target = self.root / "my notes.txt"
        target.write_text("space path", encoding="utf-8")

        result = call_read_file(target)

        self.assertIn("space path", result)

    def test_offset_pagination(self) -> None:
        target = self.root / "page.txt"
        content = "0123456789" * 30
        target.write_text(content, encoding="utf-8")

        result = call_read_file(target, offset=100, max_chars=100)

        self.assertIn("[文件]", result)
        self.assertIn("共 300 字符", result)
        self.assertIn("offset=200", result)
        page = result.split("\n", 1)[1]
        self.assertEqual(page, content[100:200])

    def test_empty_file(self) -> None:
        target = self.root / "empty.txt"
        target.write_bytes(b"")

        result = call_read_file(target)

        self.assertIn("(空文件)", result)
        self.assertEqual(result.count("空文件"), 1)

    def test_offset_beyond_end(self) -> None:
        target = self.root / "short.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_read_file(target, offset=100)

        self.assertIn("文件末尾", result)

    def test_offset_reads_beyond_one_megabyte(self) -> None:
        target = self.root / "large.txt"
        target.write_text("a" * 1_200_000, encoding="utf-8")

        result = call_read_file(target, offset=1_100_000, max_chars=100)

        self.assertIn("a" * 100, result)
        self.assertNotIn("文件过大", result)


if __name__ == "__main__":
    unittest.main()
