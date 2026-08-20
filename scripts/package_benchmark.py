"""生成与成员实现无关、可复现的 Mini-Drop 团队统一测试集压缩包。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import hashlib
import json
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.1.2"
RELEASE_DATE = date(2026, 8, 2)
PREFIX = f"Mini-Drop统一诊断测试集-v{VERSION}"
OUT = (
    ROOT
    / "artifacts"
    / "benchmarks"
    / f"{PREFIX}-团队共享版-{RELEASE_DATE:%Y%m%d}.zip"
)
FIXED_ZIP_TIME = (2026, 8, 2, 0, 0, 0)
entries: list[tuple[str, bytes]] = []


def add_bytes(archive_path: str, data: bytes) -> None:
    entries.append((archive_path.replace("\\", "/"), data))


def add_text(archive_path: str, content: str) -> None:
    add_bytes(archive_path, content.replace("\r\n", "\n").encode("utf-8"))


def add_json(archive_path: str, payload: object) -> None:
    add_text(archive_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def shared_case(payload: dict) -> dict:
    """删除本仓库实现细节，只保留跨实现可复用的故障契约。"""
    result = deepcopy(payload)
    if result.get("source_id") == "mini-drop-golden":
        result["source_id"] = "synthetic-fixture"
    trigger = result.get("trigger", {})
    if trigger.get("adapter") == "mini_drop_golden":
        trigger["adapter"] = "implementation_defined"
    return result


def build_manifest() -> dict:
    manifest = json.loads(
        (ROOT / "benchmarks" / "unified_manifest.json").read_text(encoding="utf-8")
    )
    manifest["dataset"] = "mini-drop-team-unified-diagnosis-benchmark"
    manifest["version"] = VERSION
    manifest["distribution"] = "implementation_neutral_team_shared"
    for source in manifest["sources"]:
        if source["source_id"] == "mini-drop-golden":
            source.update(
                {
                    "source_id": "synthetic-fixture",
                    "url": None,
                    "purpose": "implementation_neutral_contract_replay",
                    "license": "team-authored test fixture",
                    "resource_profile": "each implementation may create equivalent local input",
                }
            )
    for case in manifest["core_cases"]:
        if case["source_id"] == "mini-drop-golden":
            case["source_id"] = "synthetic-fixture"
        case["case_file"] = f"cases/{case['case_id']}.json"
    return manifest


CASE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://mini-drop.example/schema/diagnosis-case-v1.json",
    "title": "Mini-Drop unified diagnosis case",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "case_id", "title", "source_id", "fault_type",
        "query", "trigger", "topology", "evidence_plan", "oracle",
        "execution", "reference_ids",
    ],
    "properties": {
        "schema_version": {"const": "1.0"},
        "case_id": {"type": "string", "pattern": "^T[0-9]+-[A-Z]+-[0-9]{3}$"},
        "title": {"type": "string", "minLength": 3},
        "source_id": {"type": "string", "minLength": 1},
        "fault_type": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 3},
        "trigger": {"type": "object"},
        "topology": {"type": "object"},
        "evidence_plan": {"type": "object"},
        "oracle": {"type": "object"},
        "execution": {"type": "object"},
        "reference_ids": {"type": "array", "items": {"type": "string"}},
    },
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://mini-drop.example/schema/diagnosis-output-v1.json",
    "title": "Mini-Drop unified diagnosis output",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "case_id", "run_id", "implementation_id", "strategy",
        "status", "root_causes", "unknown", "snapshot_refs", "tool_calls",
        "elapsed_ms", "unsafe_auto_execute_count",
    ],
    "properties": {
        "schema_version": {"const": "1.0"},
        "case_id": {"type": "string"},
        "run_id": {"type": "string", "minLength": 1},
        "implementation_id": {"type": "string", "minLength": 1},
        "strategy": {
            "enum": ["CONSTRAINED_HYBRID", "DECISION_TREE", "EXPLORATORY", "OTHER"]
        },
        "status": {
            "enum": [
                "COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE",
                "UNSUPPORTED", "FAILED",
            ]
        },
        "root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "rank", "entity_type", "entity_id", "reason",
                    "confidence", "evidence_refs",
                ],
                "properties": {
                    "rank": {"type": "integer", "minimum": 1},
                    "entity_type": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_refs": {
                        "type": "array", "items": {"type": "string"}, "uniqueItems": True
                    },
                },
            },
        },
        "unknown": {"type": "boolean"},
        "snapshot_refs": {"type": "array", "items": {"type": "string"}},
        "tool_calls": {"type": "integer", "minimum": 0},
        "elapsed_ms": {"type": "integer", "minimum": 0},
        "unsafe_auto_execute_count": {"type": "integer", "minimum": 0},
        "notes": {"type": "string"},
    },
}

PROTOCOL = """# 统一评测协议

## 接入要求

各成员保留自己的技术栈、目录和接口，只需完成两次转换：

1. 把 `cases/*.json` 转换为本项目可执行的任务；
2. 把诊断结果转换为 `diagnosis-output.schema.json` 规定的格式。

## 正式运行

- 每个案例先预热 1 次，再正式执行至少 3 次；
- baseline、incident、verification 使用相同负载和时间窗口；
- Oracle 仅供诊断完成后的评测器读取，禁止放入模型上下文；
- 结论必须通过 `evidence_refs` 引用原始证据；
- 信息不足时填写 `INSUFFICIENT_EVIDENCE`，功能缺失时填写 `UNSUPPORTED`；
- 高权限采集或变更未经人工确认时，不计为已执行。

## 统一指标

- 根因 Top-1 / Top-3 命中率；
- 服务、实例、主机和依赖边定位准确率；
- Evidence 引用完整率；
- baseline、incident、verification 快照覆盖率；
- 无证据结论率与信息不足校准率；
- 未经确认的高风险自动执行次数；
- 诊断耗时、工具调用数和模型成本。

## 提交结果

每次正式运行输出一个 JSON 文件，建议命名为：

```text
<case_id>__<implementation_id>__<strategy>__run-<n>.json
```

不同实现的目录和接口可以不同，只需遵守统一用例与输出格式。
"""

EXAMPLE = {
    "schema_version": "1.0",
    "case_id": "T1-CPU-001",
    "run_id": "run-001",
    "implementation_id": "team-member-a",
    "strategy": "CONSTRAINED_HYBRID",
    "status": "COMPLETED",
    "root_causes": [
        {
            "rank": 1,
            "entity_type": "service",
            "entity_id": "ad-service",
            "reason": "CPU samples are concentrated in the injected high-CPU path.",
            "confidence": 0.91,
            "evidence_refs": ["snapshot-incident-001", "profile-001"],
        }
    ],
    "unknown": False,
    "snapshot_refs": [
        "snapshot-baseline-001", "snapshot-incident-001", "snapshot-verification-001"
    ],
    "tool_calls": 4,
    "elapsed_ms": 12800,
    "unsafe_auto_execute_count": 0,
    "notes": "示例仅用于说明格式，请替换为各实现产生的真实证据。",
}


def build_readme() -> str:
    return f"""# Mini-Drop 统一诊断测试集 v{VERSION}（团队共享版）

这是一份与成员代码无关的测试规范包。React、Go、Python、C++ 或其他技术栈都可以接入，也不要求使用相同目录。

## 文件夹说明

| 路径 | 内容 | 谁需要看 |
|---|---|---|
| `cases/` | 10 个统一故障输入 | 所有开发和测试成员 |
| `protocol/` | 用例 Schema、输出 Schema、评测协议 | 负责接入和评测的成员 |
| `references/` | 开源项目、论文、固定版本和许可证 | 导师、方案设计与评测人员 |
| `examples/` | 一个通用输出示例 | 第一次接入的成员 |
| `manifest.json` | 用例总目录和来源 | 评测脚本或人工检查 |
| `SHA256SUMS.txt` | 包内文件校验值 | 核对文件完整性时使用 |

## 怎么接入自己的代码

```text
cases/*.json
    -> 你的适配器
    -> 你的 Server / Agent / AI
    -> diagnosis-output JSON
    -> 按 protocol/统一评测协议.md 评分
```

若项目暂不支持某项能力，在输出中填写 `UNSUPPORTED`；证据不足时填写 `INSUFFICIENT_EVIDENCE`。禁止修改用例答案来迎合自己的实现。

## 当前范围

- 10 类故障；
- 3 种建议诊断策略；
- 每个组合正式重复 3 次，共 90 次计划执行；
- 7 个 OpenTelemetry Demo 公开故障场景；
- 3 个不依赖具体项目代码的合成回放场景。

大型外部数据不包含在压缩包中。下载地址、固定版本和许可证见 `references/开源项目论文与资料索引.md`。
"""


CHANGELOG = """# 版本说明

## v1.1.2

- 修复共享包生成脚本和包内说明文档的中文编码；
- 统一 Oracle 的 `expected_classification` 为集群位置分类口径；
- 保留 10 类故障、三阶段快照、证据引用和人工审批约束；
- 删除成员代码路径、个人运行报告和实现专用目录。

## v1.1.1

- 增加统一输入、输出 JSON Schema；
- 增加与技术栈无关的接入说明和输出示例；
- 将本地 Golden 来源转换为通用 `synthetic-fixture`。
"""


def main() -> None:
    add_json(f"{PREFIX}/manifest.json", build_manifest())

    for path in sorted((ROOT / "benchmarks" / "cases").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        add_json(f"{PREFIX}/cases/{path.name}", shared_case(payload))

    add_json(f"{PREFIX}/protocol/case.schema.json", CASE_SCHEMA)
    add_json(f"{PREFIX}/protocol/diagnosis-output.schema.json", OUTPUT_SCHEMA)
    add_text(f"{PREFIX}/protocol/统一评测协议.md", PROTOCOL)
    add_json(f"{PREFIX}/examples/diagnosis-output.example.json", EXAMPLE)
    add_text(
        f"{PREFIX}/references/开源项目论文与资料索引.md",
        (ROOT / "docs" / "benchmarks" / "sources.md").read_text(encoding="utf-8"),
    )
    add_text(f"{PREFIX}/README.md", build_readme())
    add_text(f"{PREFIX}/CHANGELOG.md", CHANGELOG)

    sums = "\n".join(
        f"{hashlib.sha256(data).hexdigest()}  {name.split('/', 1)[1]}"
        for name, data in sorted(entries)
    ) + "\n"
    add_text(f"{PREFIX}/SHA256SUMS.txt", sums)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)

    with zipfile.ZipFile(OUT, "r") as archive:
        broken = archive.testzip()
        names = archive.namelist()
    if broken:
        raise RuntimeError(f"压缩包损坏条目：{broken}")

    archive_sha256 = hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT.with_suffix(".sha256.txt").write_text(
        f"{archive_sha256}  {OUT.name}\n", encoding="utf-8"
    )

    print(f"ZIP={OUT}")
    print(f"FILES={len(names)}")
    print(f"SIZE={OUT.stat().st_size}")
    print(f"SHA256={archive_sha256}")


if __name__ == "__main__":
    main()
