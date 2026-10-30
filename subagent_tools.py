from __future__ import annotations

from langchain_core.tools import tool

from subagent_manager import SubagentError, SubagentManager


def build_subagent_tools(manager: SubagentManager) -> list:
    @tool
    def spawn_agent(task: str, allow_writes: bool = False) -> str:
        """创建一个独立子 agent 执行给定任务。

        默认只能读文件；确实需要它写文件/改 Office 时才传 allow_writes=True。
        """
        try:
            record = manager.spawn(task, allow_writes=allow_writes)
        except SubagentError as exc:
            return f"[错误] {exc}"
        return f"[已创建] {record.id} | {record.task[:80]}"

    @tool
    def list_agents() -> str:
        """列出所有子 agent 的状态、任务和结果摘要。"""
        try:
            return manager.format_table()
        except Exception as exc:  # noqa: BLE001
            return f"[错误] 查询子 agent 失败: {exc}"

    @tool
    def wait_agent(agent_id: str, timeout: int = 180) -> str:
        """等待指定子 agent 完成并返回结果；timeout 为秒，默认 180。"""
        try:
            record = manager.wait(agent_id, timeout)
        except SubagentError as exc:
            return f"[错误] {exc}"
        if record.status == "succeeded":
            return f"[完成] {record.id} | {record.result or ''}"
        if record.status == "failed":
            return f"[失败] {record.id} | {record.error or ''}"
        return f"[超时] {record.id} 仍在运行 | 任务: {record.task[:80]}"

    return [spawn_agent, list_agents, wait_agent]
