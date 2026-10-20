"""A fixed, bounded phase channel from an unprivileged decoder to its owner."""

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
        # Read at most five fixed stage records. A concurrent partial line is
        # left for the next poll; fast children are drained again after exit.
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
