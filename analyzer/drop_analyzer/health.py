"""Read the worker's heartbeat without exposing a public management port."""

import json
from pathlib import Path
import sys
import time

from .config import Config


def healthy(path: str, *, max_age: float = 45) -> bool:
    try:
        heartbeat = json.loads(Path(path).read_text(encoding="utf-8"))
        age = time.time() - heartbeat["updated_at"]
        return bool(heartbeat["ready"]) and 0 <= age <= max_age
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == "__main__":
    try:
        config = Config.load(require_credentials=False)
        sys.exit(0 if healthy(config.health_file) else 1)
    except ValueError:
        sys.exit(1)
