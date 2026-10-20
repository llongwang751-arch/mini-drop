"""只将明确登记、可丢弃的玩具夹具复制到独立测试通道。"""

import json
from pathlib import Path
import shutil

source = Path("/opt/drop-build/core-v2/core/evidence")
destination = Path("/opt/drop-build/analyzer-full/runner-fixtures")
destination.mkdir(mode=0o755, parents=True, exist_ok=True)
count = 0
for group in ("language-collected", "native-collected"):
    for manifest_path in sorted((source / group).glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        name = manifest["name"]
        if Path(name).name != name:
            raise ValueError("Fixture names must be local")
        target = destination / group / manifest_path.parent.name
        target.mkdir(mode=0o755, parents=True, exist_ok=True)
        for item in (
            manifest_path,
            manifest_path.parent / name,
            manifest_path.parent / "request.json",
        ):
            if item.exists():
                copy = target / item.name
                shutil.copyfile(item, copy)
                copy.chmod(0o644)
        count += 1
print(json.dumps({"copied_toy_artifact_sets": count}))
