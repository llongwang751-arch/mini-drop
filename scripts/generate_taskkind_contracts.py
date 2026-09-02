"""Generate all language bindings from contracts/taskkinds.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "taskkinds.json"


def _load() -> dict:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    kinds = payload.get("task_kinds") or []
    ids = [item["id"] for item in kinds]
    names = [item["name"] for item in kinds]
    if not kinds or len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise ValueError("TaskKind ids and names must be non-empty and unique")
    for item in kinds:
        if item["runner"] != item["name"]:
            raise ValueError(f"runner/name drift is not allowed: {item['name']}")
        if item["default_duration_seconds"] > item["max_duration_seconds"]:
            raise ValueError(f"invalid duration for {item['name']}")
        if item["default_sample_rate"] > item["max_sample_rate"]:
            raise ValueError(f"invalid sample rate for {item['name']}")
        filenames = item.get("artifact_filenames") or []
        if not filenames or len(filenames) != len(set(filenames)):
            raise ValueError(f"artifact_filenames must be non-empty and unique: {item['name']}")
        if any("/" in filename or "\\" in filename or filename in {".", ".."} for filename in filenames):
            raise ValueError(f"artifact_filenames must be basename-only: {item['name']}")
        if "manifest.json" not in filenames:
            raise ValueError(f"artifact_filenames must include manifest.json: {item['name']}")
    return payload


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


SPECIALIZED_OPTION_PROPERTIES = {
    "perf_cpu": {
        "event": {"type": "string", "minLength": 1, "maxLength": 128},
        "callgraph": {"enum": ["fp", "dwarf", "lbr"]},
        "subprocess": {"type": "boolean"},
    },
    "java_async": {
        "event": {"type": "string", "minLength": 1, "maxLength": 128},
    },
    "go_pprof": {
        "pprof_url": {"type": "string", "format": "uri", "maxLength": 2048},
    },
    "pyspy": {"subprocess": {"type": "boolean"}},
    "ebpf_io": {"device": {"type": "string", "maxLength": 128}},
    "memory_smaps": {
        "interval_ms": {"type": "integer", "minimum": 50, "maximum": 60000},
    },
    "sys_metrics": {
        "interval_ms": {"type": "integer", "minimum": 50, "maximum": 60000},
    },
    "continuous_perf": {
        "event": {"type": "string", "minLength": 1, "maxLength": 128},
        "callgraph": {"enum": ["fp", "dwarf", "lbr"]},
        "window_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    },
}


def _option_properties(item: dict) -> dict:
    common = {
        "timeout_sec": {
            "type": "integer",
            "minimum": 1,
            "maximum": int(item["max_duration_seconds"]) + 300,
        },
        "container_name": {"type": "string", "maxLength": 256},
        "container_type": {"type": "integer", "minimum": 0, "maximum": 16},
    }
    return common | SPECIALIZED_OPTION_PROPERTIES.get(item["name"], {})


def _go(payload: dict) -> str:
    rows = []
    for item in payload["task_kinds"]:
        string_slice = lambda values: "[]string{" + ", ".join(_q(v) for v in values) + "}"
        option_rules = []
        for name, rule in _option_properties(item).items():
            option_rules.append(
                "OptionRule{" +
                f"Name: {_q(name)}, Type: {_q(rule.get('type', 'string'))}, " +
                f"Minimum: {int(rule.get('minimum', 0))}, Maximum: {int(rule.get('maximum', 0))}, " +
                f"MinLength: {int(rule.get('minLength', 0))}, MaxLength: {int(rule.get('maxLength', 0))}, " +
                f"Enum: {string_slice(rule.get('enum', []))}, Format: {_q(rule.get('format', ''))}" +
                "}"
            )
        option_rules_literal = "[]OptionRule{" + ", ".join(option_rules) + "}"
        rows.append(
            "\t{"
            f"ProfilerType: {item['id']}, ID: {_q(item['name'])}, Label: {_q(item['display_name'])}, "
            f"ResultLabel: {_q(item['result_label'])}, Description: {_q(item['description'])}, Color: {_q(item['color'])}, "
            f"Runner: {_q(item['runner'])}, AnalysisPipeline: {_q(item['analysis_pipeline'])}, "
            f"SupportedOS: {string_slice(item['supported_os'])}, SupportedArch: {string_slice(item['supported_arch'])}, "
            f"RequiresCapabilities: {string_slice(item['requires_capabilities'])}, SupportsContainer: {str(item['supports_container']).lower()}, "
            f"DefaultDurationSec: {item['default_duration_seconds']}, MaxDurationSec: {item['max_duration_seconds']}, "
            f"DefaultSampleRate: {item['default_sample_rate']}, MaxSampleRate: {item['max_sample_rate']}, "
            f"MaxConcurrencyPerAgent: {item['max_concurrency_per_agent']}, ParameterSchema: {_q(item['parameter_schema'])}, "
            f"ArtifactFilenames: {string_slice(item['artifact_filenames'])}, "
            f"OptionRules: {option_rules_literal}, "
            f"DefaultEvent: {_q(item['default_event'])}, Flamegraph: {str(item['flamegraph']).lower()}"
            "},"
        )
    return f'''// Code generated by scripts/generate_taskkind_contracts.py; DO NOT EDIT.
package taskkind

type OptionRule struct {{
\tName string
\tType string
\tMinimum int
\tMaximum int
\tMinLength int
\tMaxLength int
\tEnum []string
\tFormat string
}}

type Kind struct {{
\tProfilerType uint32 `json:"id"`
\tID string `json:"name"`
\tLabel string `json:"display_name"`
\tResultLabel string `json:"result_label"`
\tDescription string `json:"description"`
\tColor string `json:"color"`
\tRunner string `json:"runner"`
\tAnalysisPipeline string `json:"analysis_pipeline"`
\tSupportedOS []string `json:"supported_os"`
\tSupportedArch []string `json:"supported_arch"`
\tRequiresCapabilities []string `json:"requires_capabilities"`
\tSupportsContainer bool `json:"supports_container"`
\tDefaultDurationSec int `json:"default_duration_seconds"`
\tMaxDurationSec int `json:"max_duration_seconds"`
\tDefaultSampleRate int `json:"default_sample_rate"`
\tMaxSampleRate int `json:"max_sample_rate"`
\tMaxConcurrencyPerAgent int `json:"max_concurrency_per_agent"`
\tParameterSchema string `json:"parameter_schema"`
\tArtifactFilenames []string `json:"artifact_filenames"`
\tOptionRules []OptionRule `json:"-"`
\tDefaultEvent string `json:"default_event"`
\tFlamegraph bool `json:"flamegraph"`
}}

var catalog = []Kind{{
{chr(10).join(rows)}
}}

func List() []Kind {{ items := make([]Kind, len(catalog)); copy(items, catalog); return items }}
func Lookup(id string) (Kind, bool) {{ for _, item := range catalog {{ if item.ID == id {{ return item, true }} }}; return Kind{{}}, false }}
'''


def _cpp(payload: dict) -> str:
    rows = "\n".join(
        f'    TaskKind{{{item["id"]}, {_q(item["name"])}, {_q(item["analysis_pipeline"])}, {_q(item["default_event"])}, {len(item["artifact_filenames"])}}},'
        for item in payload["task_kinds"]
    )
    artifact_rows = "\n".join(
        f'    TaskKindArtifactFilename{{{item["id"]}, {_q(filename)}}},'
        for item in payload["task_kinds"]
        for filename in item["artifact_filenames"]
    )
    artifact_count = sum(
        len(item["artifact_filenames"]) for item in payload["task_kinds"]
    )
    return f'''// Code generated by scripts/generate_taskkind_contracts.py; DO NOT EDIT.
#pragma once
#include <array>
#include <string_view>
namespace mini_drop_contract {{
struct TaskKind {{ int profiler_type; std::string_view name; std::string_view analysis_pipeline; std::string_view default_event; int artifact_count; }};
struct TaskKindArtifactFilename {{ int profiler_type; std::string_view filename; }};
inline constexpr std::array<TaskKind, {len(payload['task_kinds'])}> kTaskKinds{{{{
{rows}
}}}};
inline constexpr std::array<TaskKindArtifactFilename, {artifact_count}> kTaskKindArtifactFilenames{{{{
{artifact_rows}
}}}};
inline constexpr const TaskKind* find_by_name(std::string_view name) {{
  for (const auto& item : kTaskKinds) if (item.name == name) return &item;
  return nullptr;
}}
inline constexpr const TaskKind* find_by_profiler_type(int value) {{
  for (const auto& item : kTaskKinds) if (item.profiler_type == value) return &item;
  return nullptr;
}}
inline constexpr bool is_artifact_filename(int profiler_type, std::string_view filename) {{
  for (const auto& item : kTaskKindArtifactFilenames) {{
    if (item.profiler_type == profiler_type && item.filename == filename) return true;
  }}
  return false;
}}
}}  // namespace mini_drop_contract
'''


def _python(payload: dict) -> str:
    serialized = json.dumps(payload["task_kinds"], ensure_ascii=False, indent=2)
    return f'''# Code generated by scripts/generate_taskkind_contracts.py; DO NOT EDIT.
import json

TASK_KINDS = json.loads(r\'''{serialized}\''')
BY_NAME = {{item["name"]: item for item in TASK_KINDS}}
BY_PROFILER_TYPE = {{item["id"]: item for item in TASK_KINDS}}

def profiler_type_for(name: str) -> int:
    item = BY_NAME.get(name)
    if item is None:
        raise ValueError(f"unknown TaskKind: {{name}}")
    return int(item["id"])

def name_for_profiler_type(value: int) -> str:
    item = BY_PROFILER_TYPE.get(int(value))
    if item is None:
        raise ValueError(f"unknown TaskKind profiler_type: {{value}}")
    return str(item["name"])
'''


def _javascript(payload: dict) -> str:
    serialized = json.dumps(payload["task_kinds"], ensure_ascii=False, indent=2)
    return f'''// Code generated by scripts/generate_taskkind_contracts.py; DO NOT EDIT.
export const TASK_KINDS = Object.freeze({serialized});
'''


def _proto(payload: dict) -> str:
    values = "\n".join(
        f"  TASK_KIND_{item['name'].upper()} = {item['id']};"
        for item in payload["task_kinds"]
    )
    return f'''// Code generated by scripts/generate_taskkind_contracts.py; DO NOT EDIT.
syntax = "proto3";
package mini_drop;
option go_package = "github.com/jiangyulin1/mini-drop/proto;mini_drop";
enum TaskKindProfiler {{
{values}
}}
'''


def _parameter_schema(item: dict) -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://mini-drop.local/contracts/task-parameters/{item['name']}.schema.json",
        "title": f"Mini-Drop {item['name']} task parameters",
        "type": "object",
        "additionalProperties": False,
        "required": ["target_pid", "duration_sec", "sample_rate"],
        "properties": {
            "target_pid": {"type": "integer", "minimum": 1, "maximum": 4194304},
            "duration_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": int(item["max_duration_seconds"]),
                "default": int(item["default_duration_seconds"]),
            },
            "sample_rate": {
                "type": "integer",
                "minimum": 1,
                "maximum": int(item["max_sample_rate"]),
                "default": int(item["default_sample_rate"]),
            },
            "options": {
                "type": "object",
                # Diagnostic campaigns attach provenance keys here. Known
                # runner parameters are typed; extension metadata is retained.
                "additionalProperties": True,
                "properties": _option_properties(item),
                "default": {},
            },
        },
        "x-task-kind": item["name"],
        "x-contract-version": "1.0.0",
    }
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = _load()
    outputs = {
        ROOT / "apiserver" / "internal" / "taskkind" / "catalog.go": _go(payload),
        ROOT / "native" / "generated" / "taskkind_contract.h": _cpp(payload),
        ROOT / "server" / "app" / "generated" / "taskkind_contract.py": _python(payload),
        ROOT / "web" / "src" / "generated" / "taskKinds.js": _javascript(payload),
        ROOT / "proto" / "taskkind.proto": _proto(payload),
    }
    for item in payload["task_kinds"]:
        outputs[ROOT / item["parameter_schema"]] = _parameter_schema(item)
    stale = []
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        raise SystemExit("stale TaskKind generated files: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
