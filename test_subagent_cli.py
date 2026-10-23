from __future__ import annotations

import unittest

from subagent_cli import format_agent_notification, format_agent_table
from subagent_store import AgentRecord


class SubagentCliTests(unittest.TestCase):
    def _record(self, **kwargs):
        base = dict(
            id="agent-20261023-201500-a1b2",
            task="整理周报",
            status="succeeded",
            created_at="2026-10-23T20:15:00+08:00",
            started_at="2026-10-23T20:15:01+08:00",
            finished_at="2026-10-23T20:15:31+08:00",
            result="已生成周报.docx",
        )
        base.update(kwargs)
        return AgentRecord(**base)

    def test_empty_table(self):
        self.assertIn("没有子 agent", format_agent_table([]))

    def test_table_has_columns_and_values(self):
        table = format_agent_table([self._record()])
        self.assertIn("ID", table)
        self.assertIn("状态", table)
        self.assertIn("succeeded", table)
        self.assertIn("整理周报", table)
        self.assertIn("30s", table)
        self.assertIn("已生成周报.docx", table)

    def test_long_text_is_truncated(self):
        table = format_agent_table([self._record(task="很长" * 50, result="结果" * 100)])
        self.assertIn("…", table)

    def test_notification_success_and_failure(self):
        ok = format_agent_notification(self._record())
        self.assertIn("[子agent]", ok)
        self.assertIn("完成", ok)
        bad = format_agent_notification(
            self._record(status="failed", result=None, error="boom")
        )
        self.assertIn("失败", bad)
        self.assertIn("boom", bad)


if __name__ == "__main__":
    unittest.main()
