"""测试用假 worker：根据任务前缀写成功/失败，或直接崩溃。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from subagent_store import SubagentStore, now_iso


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

    if record.task.startswith("crash"):
        return 1
    if record.task.startswith("slow"):
        time.sleep(2.0)
    else:
        time.sleep(0.05)
    if record.task.startswith("fail"):
        record.status = "failed"
        record.error = "fake failure"
    else:
        record.status = "succeeded"
        record.result = f"done:{record.task}"
    record.finished_at = now_iso()
    store.save(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
