r"""read_csv / read_json 结构化读取工具的单元测试。

运行：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_read_structured
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from graph_agent import read_csv, read_json

_WORKSPACE = Path(__file__).resolve().parent.parent


def call_tool(tool, path: Path | str, **kwargs) -> str:
    """调用 LangChain 工具对象。"""
    return tool.invoke({"path": str(path), **kwargs})


class CsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_structured_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_csv_shows_columns_and_rows(self) -> None:
        target = self.root / "people.csv"
        target.write_text(
            "姓名,年龄,城市\n张三,30,北京\n李四,25,上海\n",
            encoding="utf-8",
        )

        result = call_tool(read_csv, target)

        self.assertIn("[CSV]", result)
        self.assertIn("数据行 2", result)
        self.assertIn("列数 3", result)
        self.assertIn("[列]", result)
        self.assertIn("姓名", result)
        self.assertIn("张三", result)
        self.assertIn("30", result)
        self.assertIn("上海", result)

    def test_csv_detects_semicolon_delimiter(self) -> None:
        target = self.root / "scores.csv"
        target.write_text("name;score\nAlice;90\nBob;80\n", encoding="utf-8")

        result = call_tool(read_csv, target)

        self.assertIn("分号", result)
        self.assertIn("Alice", result)
        self.assertIn("90", result)

    def test_csv_max_rows(self) -> None:
        lines = ["name,score"] + [
            f"user{i},{i}" for i in range(1, 6)
        ]
        target = self.root / "many.csv"
        target.write_text("\n".join(lines), encoding="utf-8")

        result = call_tool(read_csv, target, max_rows=2)

        self.assertIn("user1", result)
        self.assertIn("user2", result)
        self.assertNotIn("user3", result)
        self.assertIn("仅显示", result)

    def test_csv_missing_file(self) -> None:
        result = call_tool(read_csv, self.root / "missing.csv")

        self.assertIn("[错误]", result)
        self.assertIn("不存在", result)


class JsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_structured_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_json_object_shows_keys_and_types(self) -> None:
        target = self.root / "profile.json"
        target.write_text(
            '{"name": "张三", "age": 30, "tags": ["a", "b"], "active": true}',
            encoding="utf-8",
        )

        result = call_tool(read_json, target)

        self.assertIn("[JSON]", result)
        self.assertIn("类型 object", result)
        self.assertIn("[键] 4 个", result)
        self.assertIn("name", result)
        self.assertIn("str", result)
        self.assertIn("age", result)
        self.assertIn("int", result)
        self.assertIn("tags", result)
        self.assertIn("array(2)", result)
        self.assertIn("active", result)
        self.assertIn("bool", result)
        self.assertIn("张三", result)

    def test_json_array_summary(self) -> None:
        target = self.root / "items.json"
        target.write_text("[1, 2, 3]", encoding="utf-8")

        result = call_tool(read_json, target)

        self.assertIn("类型 array", result)
        self.assertIn("长度 3", result)

    def test_json_invalid_returns_error(self) -> None:
        target = self.root / "broken.json"
        target.write_text("{broken", encoding="utf-8")

        result = call_tool(read_json, target)

        self.assertIn("[错误]", result)
        self.assertIn("JSON", result)

    def test_json_missing_file(self) -> None:
        result = call_tool(read_json, self.root / "missing.json")

        self.assertIn("[错误]", result)
        self.assertIn("不存在", result)


if __name__ == "__main__":
    unittest.main()
