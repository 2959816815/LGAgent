from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from graph_agent import build_graph
from subagent_store import SubagentStore, now_iso

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个子 agent，只负责完成用户交给你的一个任务。"
    "你可以使用提供的文件和 Office 工具；完成后用简洁中文给出结果。"
    "你不能创建子 agent，也不要要求用户继续输入。"
)


def _last_ai_text(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        content = message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--model-key", default=None)
    args = parser.parse_args(argv)

    store = SubagentStore(Path(args.project_root) / ".agents")
    record = store.load(args.agent_id)
    record.pid = os.getpid()
    record.started_at = now_iso()
    store.save(record)

    try:
        graph = build_graph(
            include_subagent_tools=False,
            model_key=args.model_key,
        )
        result = graph.invoke(
            {
                "messages": [
                    SystemMessage(content=SUBAGENT_SYSTEM_PROMPT),
                    HumanMessage(content=record.task),
                ]
            },
            config={"recursion_limit": 25},
        )
        messages = [
            message
            for message in result["messages"]
            if not isinstance(message, SystemMessage)
        ]
        record.messages = messages
        record.result = _last_ai_text(messages)
        record.status = "succeeded"
        record.error = None
        code = 0
    except Exception as exc:  # noqa: BLE001
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}"
        code = 1
    finally:
        record.finished_at = now_iso()
        store.save(record)
    return code


if __name__ == "__main__":
    sys.exit(main())
