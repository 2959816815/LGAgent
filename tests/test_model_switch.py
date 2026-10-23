from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from graph_agent import ModelRuntime, run_chat_loop
from model_registry import ModelConfigError
from session_store import SessionStore

USED_GRAPHS: list[str] = []


class RecordingGraph:
    def __init__(self, key: str):
        self.key = key

    def invoke(self, state, config):
        USED_GRAPHS.append(self.key)
        messages = list(state["messages"])
        messages.append(AIMessage(content=f"reply:{self.key}"))
        return {"messages": messages}


class FakeManager:
    def __init__(self):
        self.keys: list[str] = []

    def set_model_key(self, key: str) -> None:
        self.keys.append(key)


def fake_build(manager, model_key=None):
    return RecordingGraph(model_key)


def failing_build(manager, model_key=None):
    if model_key == "qwen":
        raise ModelConfigError("未找到 DASHSCOPE_API_KEY，无法切换到 千问多模态。")
    return RecordingGraph(model_key)


class ModelRuntimeAndCommandTests(unittest.TestCase):
    def setUp(self):
        USED_GRAPHS.clear()
        self.root = Path(__file__).resolve().parent.parent / f".test_model_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SessionStore(self.root)
        self.session = self.store.create("模型测试")
        self.outputs: list[str] = []
        self.manager = FakeManager()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, inputs: list[str], runtime: ModelRuntime) -> None:
        iterator = iter(inputs)
        run_chat_loop(
            self.store,
            runtime.graph,
            self.session,
            input_fn=lambda prompt: next(iterator),
            output=self.outputs.append,
            runtime=runtime,
        )

    def test_runtime_initializes_and_switches(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self.assertEqual(runtime.key, "local")
            self.assertEqual(runtime.graph.key, "local")
            profile = runtime.switch("2")
        self.assertEqual(profile.key, "deepseek")
        self.assertEqual(runtime.key, "deepseek")
        self.assertEqual(runtime.graph.key, "deepseek")
        self.assertEqual(self.manager.keys, ["deepseek"])

    def test_failed_switch_keeps_previous_state(self):
        with patch("graph_agent.build_graph", side_effect=failing_build):
            runtime = ModelRuntime(self.manager, "local")
            with self.assertRaises(ModelConfigError):
                runtime.switch("qwen")
        self.assertEqual(runtime.key, "local")
        self.assertEqual(runtime.graph.key, "local")
        self.assertEqual(self.manager.keys, [])

    def test_same_model_is_noop(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            old_graph = runtime.graph
            profile = runtime.switch("1")
        self.assertIs(runtime.graph, old_graph)
        self.assertEqual(profile.key, "local")
        self.assertEqual(self.manager.keys, [])

    def test_model_list_command(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model", "exit"], runtime)
        output = "\n".join(self.outputs)
        self.assertIn("[模型] 当前: local", output)
        self.assertIn("deepseek", output)
        self.assertIn("qwen3-vl-plus", output)

    def test_switch_command_uses_new_graph(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model 2", "你好", "exit"], runtime)
        output = "\n".join(self.outputs)
        self.assertIn("[模型] 已切换: deepseek-v4.1-flash", output)
        self.assertIn("[助手] reply:deepseek", output)
        self.assertEqual(USED_GRAPHS, ["deepseek"])
        self.assertEqual(self.manager.keys, ["deepseek"])

    def test_invalid_selector_does_not_change_model(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model nope", "exit"], runtime)
        self.assertIn("[错误] 未知模型", "\n".join(self.outputs))
        self.assertEqual(runtime.key, "local")

    def test_switch_preserves_session_messages(self):
        self.session.messages = [HumanMessage(content="旧消息")]
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            runtime.switch("deepseek")
        self.assertEqual([m.content for m in self.session.messages], ["旧消息"])


if __name__ == "__main__":
    unittest.main()
