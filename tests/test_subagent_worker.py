from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from subagent_store import SubagentStore
from subagent_worker import _last_ai_text, main


class FakeGraph:
    def invoke(self, state, config):
        return {
            "messages": [
                *state["messages"],
                AIMessage(content="任务完成"),
            ]
        }


class SubagentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_worker_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SubagentStore(self.root / ".agents")
        self.record = self.store.create("写个报告")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_last_ai_text(self):
        messages = [HumanMessage(content="x"), AIMessage(content="结果")]
        self.assertEqual(_last_ai_text(messages), "结果")

    def test_main_success(self):
        with patch("subagent_worker.build_graph", return_value=FakeGraph()):
            code = main(["--agent-id", self.record.id, "--project-root", str(self.root)])
        self.assertEqual(code, 0)
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.status, "succeeded")
        self.assertEqual(loaded.result, "任务完成")
        self.assertEqual(loaded.messages[-1].content, "任务完成")

    def test_main_failure(self):
        with patch("subagent_worker.build_graph", side_effect=RuntimeError("boom")):
            code = main(["--agent-id", self.record.id, "--project-root", str(self.root)])
        self.assertEqual(code, 1)
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("boom", loaded.error or "")

    def test_main_forwards_model_key(self):
        with patch("subagent_worker.build_graph", return_value=FakeGraph()) as build:
            code = main(
                [
                    "--agent-id",
                    self.record.id,
                    "--project-root",
                    str(self.root),
                    "--model-key",
                    "deepseek",
                ]
            )
        self.assertEqual(code, 0)
        build.assert_called_once_with(
            include_subagent_tools=False,
            model_key="deepseek",
            read_only=True,
        )


if __name__ == "__main__":
    unittest.main()
