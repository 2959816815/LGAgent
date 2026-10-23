"""graph_agent 会话主循环的集成测试（fake graph + 临时 store）。"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage

from graph_agent import run_chat_loop
from session_store import SessionStore


class FakeGraph:
    def invoke(self, state, config):
        messages = list(state["messages"])
        messages.append(AIMessage(content="收到"))
        return {"messages": messages}


class SessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_flow_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SessionStore(self.root)
        self.session = self.store.create("新会话")
        self.outputs: list[str] = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, inputs: list[str]) -> None:
        iterator = iter(inputs)
        run_chat_loop(
            self.store,
            FakeGraph(),
            self.session,
            input_fn=lambda prompt: next(iterator),
            output=self.outputs.append,
        )

    def test_chat_saves_history_and_auto_title(self):
        self._run(["你好", "exit"])
        loaded = self.store.load(self.session.id)
        self.assertEqual(loaded.title, "你好")
        self.assertEqual([m.type for m in loaded.messages], ["human", "ai"])
        self.assertEqual(loaded.messages[0].content, "你好")
        self.assertIn("[助手] 收到", "\n".join(self.outputs))

    def test_new_command_creates_and_switches(self):
        self._run(["/new 工作", "exit"])
        metas = self.store.list_sessions()
        self.assertEqual(len(metas), 2)
        active = self.store.get_active_id()
        self.assertIsNotNone(active)
        self.assertEqual(self.store.load(active).title, "工作")
        self.assertIn("已新建", "\n".join(self.outputs))

    def test_delete_current_falls_back(self):
        second = self.store.create("B")
        self.store.set_active_id(second.id)
        self.session = second
        self._run([f"/delete {second.id}", "y", "exit"])
        metas = self.store.list_sessions()
        self.assertEqual(len(metas), 1)
        self.assertEqual(self.store.get_active_id(), metas[0].id)
        self.assertEqual(metas[0].title, "新会话")

    def test_history_command_after_chat(self):
        self._run(["你好", "/history", "exit"])
        output = "\n".join(self.outputs)
        self.assertIn("[历史]", output)
        self.assertIn("[用户] 你好", output)

    def test_agents_command_without_manager(self):
        self._run(["/agents", "exit"])
        self.assertIn("没有子 agent", "\n".join(self.outputs))


if __name__ == "__main__":
    unittest.main()
