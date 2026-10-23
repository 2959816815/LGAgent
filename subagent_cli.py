from __future__ import annotations

from datetime import datetime

from subagent_store import AgentRecord

ID_WIDTH = 30
STATUS_WIDTH = 10
TASK_WIDTH = 20
DURATION_WIDTH = 8
RESULT_WIDTH = 34


def _short(text: str | None, width: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)] + "…"


def _duration(record: AgentRecord) -> str:
    try:
        start = datetime.fromisoformat(record.started_at or record.created_at)
        end = (
            datetime.fromisoformat(record.finished_at)
            if record.finished_at
            else datetime.now().astimezone()
        )
        seconds = max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m{sec:02d}s"


def format_agent_table(records: list[AgentRecord]) -> str:
    if not records:
        return "[子agent] 当前没有子 agent。"
    lines = [
        f"{'ID':<{ID_WIDTH}} {'状态':<{STATUS_WIDTH}} "
        f"{'任务':<{TASK_WIDTH}} {'耗时':<{DURATION_WIDTH}} 结果"
    ]
    for record in records:
        result = record.result if record.status == "succeeded" else record.error
        lines.append(
            f"{record.id:<{ID_WIDTH}} {record.status:<{STATUS_WIDTH}} "
            f"{_short(record.task, TASK_WIDTH):<{TASK_WIDTH}} "
            f"{_duration(record):<{DURATION_WIDTH}} "
            f"{_short(result, RESULT_WIDTH)}"
        )
    return "\n".join(lines)


def format_agent_notification(record: AgentRecord) -> str:
    if record.status == "succeeded":
        return f"[子agent] {record.id} 完成: {_short(record.result, 100)}"
    if record.status == "failed":
        return f"[子agent] {record.id} 失败: {_short(record.error, 100)}"
    return ""
