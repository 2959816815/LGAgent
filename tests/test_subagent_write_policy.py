"""子 agent 的写权限策略：默认只读，显式开启才允许写文件。"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from graph_agent import select_tools
from subagent_store import SubagentStore
from subagent_worker import main


class SelectToolsTests(unittest.TestCase):
    def test_read_only_excludes_write_tools(self) -> None:
        names = {tool.name for tool in select_tools(read_only=True)}

        self.assertNotIn("write_file", names)
        self.assertNotIn("edit_docx", names)
        self.assertNotIn("download_file", names)
        self.assertIn("read_file", names)
        self.assertIn("web_search", names)

    def test_default_keeps_write_tools(self) -> None:
        names = {tool.name for tool in select_tools()}

        self.assertIn("write_file", names)
        self.assertIn("download_file", names)


class FakeGraph:
    def invoke(self, state, config):
        return {"messages": [*state["messages"], AIMessage(content="完成")]}


class WorkerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent / f".test_policy_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SubagentStore(self.root / ".agents")
        self.record = self.store.create("任务")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, extra: list[str]) -> int:
        return main(
            ["--agent-id", self.record.id, "--project-root", str(self.root), *extra]
        )

    def test_worker_defaults_to_read_only(self) -> None:
        with patch("subagent_worker.build_graph", return_value=FakeGraph()) as build:
            code = self._run([])

        self.assertEqual(code, 0)
        self.assertTrue(build.call_args.kwargs.get("read_only"))

    def test_worker_can_enable_writes(self) -> None:
        with patch("subagent_worker.build_graph", return_value=FakeGraph()) as build:
            code = self._run(["--allow-writes"])

        self.assertEqual(code, 0)
        self.assertFalse(build.call_args.kwargs.get("read_only"))


if __name__ == "__main__":
    unittest.main()
