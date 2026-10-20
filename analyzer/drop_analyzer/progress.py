"""低权限解码子进程向父进程报告阶段的固定、有界通道。"""

import json
from pathlib import Path

from .errors import AnalysisError


HPROF_STAGES = ("parse", "index", "graph", "dominator", "emit")


def write_stage(work_dir, stage):
    if stage not in HPROF_STAGES:
        raise AnalysisError("INVALID_PROGRESS", "Unregistered analysis stage")
    path = Path(work_dir) / "analysis-progress.jsonl"
    with path.open("a", encoding="utf8") as output:
        output.write(json.dumps({"stage": stage}, separators=(",", ":")) + "\n")


class ProgressReader:
    def __init__(self, work_dir, callback):
        self.path = Path(work_dir) / "analysis-progress.jsonl"
        self.callback = callback
        self.offset = 0
        self.count = 0

    def drain(self):
        if self.callback is None or not self.path.exists():
            return
        if self.path.is_symlink() or self.path.stat().st_size > 4096:
            raise AnalysisError(
                "INVALID_PROGRESS", "Analysis progress exceeded its limit"
            )
        # 最多读取五条固定阶段记录；并发写入的半行留待下次轮询，
        # 子进程快速退出后还会再次读尽已写入记录。
        data = self.path.read_bytes()[self.offset :]
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            try:
                item = json.loads(line)
                stage = item["stage"]
                if self.count >= len(HPROF_STAGES) or stage != HPROF_STAGES[self.count]:
                    raise ValueError("Unregistered or unordered stage")
            except (ValueError, KeyError, TypeError):
                raise AnalysisError(
                    "INVALID_PROGRESS", "Invalid analysis progress record"
                )
            self.callback(stage)
            self.offset += len(line)
            self.count += 1
