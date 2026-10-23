from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage

from subagent_manager import SubagentError
from subagent_store import AgentRecord
from subagent_tools import build_subagent_tools


class FakeManager:
    def __init__(self, record: AgentRecord | None = None, error: str | None = None):
        self.record = record
        self.error = error
        self.spawned: list[str] = []

    def spawn(self, task: str) -> AgentRecord:
        if self.error:
            raise SubagentError(self.error)
        self.spawned.append(task)
        return self.record

    def format_table(self) -> str:
        return "[子agent] 当前没有子 agent。"

    def wait(self, agent_id: str, timeout: int = 180) -> AgentRecord:
        if self.error:
            raise SubagentError(self.error)
        return self.record


class SubagentToolsTests(unittest.TestCase):
    def _tools(self, manager):
        return {tool.name: tool for tool in build_subagent_tools(manager)}

    def test_spawn_agent_success(self):
        record = AgentRecord(id="agent-20261023-201500-a1b2", task="整理周报")
        tools = self._tools(FakeManager(record))
        result = tools["spawn_agent"].invoke({"task": "整理周报"})
        self.assertIn("[已创建]", result)
        self.assertIn(record.id, result)

    def test_spawn_agent_error(self):
        tools = self._tools(FakeManager(error="已满"))
        result = tools["spawn_agent"].invoke({"task": "x"})
        self.assertIn("[错误]", result)
        self.assertIn("已满", result)

    def test_list_agents(self):
        tools = self._tools(FakeManager())
        self.assertIn("没有子 agent", tools["list_agents"].invoke({}))

    def test_wait_agent_statuses(self):
        ok = AgentRecord(
            id="agent-20261023-201500-a1b2",
            task="t",
            status="succeeded",
            result="好了",
        )
        tools = self._tools(FakeManager(ok))
        self.assertIn("[完成]", tools["wait_agent"].invoke({"agent_id": ok.id}))

        failed = AgentRecord(
            id="agent-20261023-201500-a1b2",
            task="t",
            status="failed",
            error="boom",
        )
        tools = self._tools(FakeManager(failed))
        self.assertIn("[失败]", tools["wait_agent"].invoke({"agent_id": failed.id}))


if __name__ == "__main__":
    unittest.main()
