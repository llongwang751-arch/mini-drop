"""不导入目标代码，直接比较已安装发布包与经过审查的源码。"""

import hashlib
import json
from pathlib import Path


def files(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
    }


installed = files(Path("/app/drop_analyzer"))
reviewed = files(Path("/workspace/analyzer/drop_analyzer"))
report = {
    "installed_source_files": len(installed),
    "reviewed_source_files": len(reviewed),
    "matches": installed == reviewed,
    "differences": sorted(
        name
        for name in installed.keys() | reviewed.keys()
        if installed.get(name) != reviewed.get(name)
    ),
    "source_tree_sha256": hashlib.sha256(
        json.dumps(reviewed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest(),
}
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["matches"] and installed else 1)
