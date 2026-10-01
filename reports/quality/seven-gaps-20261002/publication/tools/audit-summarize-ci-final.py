"""Recompute bounded CI measurements and distinguish main JUnit from fixtures."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

STAGE = Path(__file__).resolve().parent
inventory = STAGE / "ci-final-evidence-download-inventory.json"
assert inventory.exists(), "Collect final-run official evidence first"
summary = json.loads(inventory.read_text())
assert summary["source_head"] == "35f21b82997349b2f8e8010ae834e27a25fafc8f"
assert summary["run_id"] == 36907241773
official = json.loads((STAGE / "ci-artifacts-final" / "official-artifacts-36907241773.json").read_text())
integrity = []
file_count = 0
for row in summary["artifacts"]:
    metadata = next(item for item in official["artifacts"] if item["id"] == row["id"])
    archive = (STAGE / row["archive_path"]).read_bytes()
    digest = hashlib.sha256(archive).hexdigest()
    assert digest == row["archive_sha256"] and metadata["digest"] == "sha256:" + digest
    assert len(archive) == metadata["size_in_bytes"]
    for item in row["files"]:
        path = STAGE / "ci-artifacts-final" / row["name"] / item["path"]
        path = Path("\\\\?\\" + str(path.resolve())) if os.name == "nt" else path
        raw = path.read_bytes()
        assert len(raw) == item["size"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
        file_count += 1
    integrity.append({"artifact_id": row["id"], "official_digest": metadata["digest"],
                      "local_sha256": digest, "bytes": len(archive), "verified": True})
for row in summary["logs"]:
    assert hashlib.sha256((STAGE / row["path"]).read_bytes()).hexdigest() == row["sha256"]
summary["official_archive_integrity"] = integrity
summary["extracted_file_hashes_verified"] = file_count
summary["job_log_hashes_verified"] = len(summary["logs"])


def artifact(prefix):
    return next(row for row in summary["artifacts"] if row["name"].startswith(prefix))


def document(prefix, path):
    row = artifact(prefix)
    pinned = next(file for file in row["files"] if file["path"] == path)
    raw = (STAGE / "ci-artifacts-final" / row["name"] / path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pinned["sha256"]
    return json.loads(raw)


main_reports = {"python-quality-": "python-all/junit.xml", "postgres-concurrency-": "python.junit.xml",
                "native-agent-binary-": "ctest.xml"}
suite_counts = {}
for prefix, path in main_reports.items():
    row = artifact(prefix)
    for junit in row["junit"]:
        junit["main_suite_report"] = junit["path"] == path
        junit["scope"] = "ACTUAL_SUITE_EXECUTION" if junit["main_suite_report"] else "AUXILIARY_TEST_FIXTURE_XML_NOT_AGGREGATED"
    count = next(j for j in row["junit"] if j["main_suite_report"])
    assert count["failed"] == 0
    suite_counts[prefix.rstrip("-")] = deepcopy(count)
assert suite_counts["postgres-concurrency"]["skipped"] == suite_counts["native-agent-binary"]["skipped"] == 0
assert suite_counts["python-quality"]["skipped"] == 16  # Skips are never counted as passed.
react_log = next(row for row in summary["logs"] if row["name"] == "Web (React)")
react = next(re.search(r"Tests\s+(\d+) passed", line) for line in react_log["summary_lines"] if "Tests " in line)
suite_counts["web-react"] = {"passed": int(react[1]), "skipped": 0, "failed": 0, "source": react_log["path"]}
browser = document("web-browser-", "result.json")
assert browser["passed"] and not browser["browserExceptions"]
suite_counts["web-chromium"] = {"passed": len(browser["checks"]), "scope": browser["scope"], "browser_exceptions": 0}

runtime_windows = []
for runtime in ("python", "java", "cpp"):
    report = document("observation-controls-", runtime + "/report.json")
    assert report["passed"] and report["cleanup_verified"]
    before, after = report["observations"][-2:]
    operations = after["io_operations"] - before["io_operations"]
    duration = after["io_operation_duration_ms_total"] - before["io_operation_duration_ms_total"]
    assert operations > 0 and duration >= 0
    assert report["io_window"]["operations"] == operations and abs(report["io_window"]["average_latency_ms"] - duration / operations) < 1e-12
    assert after["io_failures"] == 0 and after["io_bytes_written"] > 64 * 1024**2
    result = {"runtime": runtime, "passed": True, "cleanup_verified": True,
              "image_id": report["image_id"], "runner_sha256": report["runner_sha256"],
              "io_operations_total": after["io_operations"], "io_bytes_written_total": after["io_bytes_written"],
              "io_failures": 0, "io_window": deepcopy(report["io_window"]), "boundary": report["boundary"]}
    if "lock_window" in report:
        window = report["lock_window"]
        result["lock_window"] = {key + "_delta": window["after"][key] - window["before"][key]
                                 for key in ("lock_wait_ms", "lock_contentions", "lock_acquisitions")}
        result["lock_window"]["average_wait_ms"] = result["lock_window"]["lock_wait_ms_delta"] / result["lock_window"]["lock_acquisitions_delta"]
        assert all(value > 0 for value in result["lock_window"].values())
    if "cgroup_window" in report:
        window = report["cgroup_window"]
        result["cgroup_window"] = {key + "_delta": int(window["after"][key]) - int(window["before"][key])
                                   for key in ("nr_periods", "nr_throttled", "throttled_usec")}
        result["cgroup_window"]["cpu_max"] = window["cpu_max"]
        assert result["cgroup_window"]["nr_throttled_delta"] > 0 and result["cgroup_window"]["throttled_usec_delta"] > 0
        result["peer_cpu_ticks_delta"] = report["peer_window"]["after"]["peer_cpu_ticks"] - report["peer_window"]["before"]["peer_cpu_ticks"]
        assert result["peer_cpu_ticks_delta"] > 0
    runtime_windows.append(result)
summary["status"] = "VERIFIED"
summary["official_success_jobs"] = 14
assert len(summary["logs"]) == 14
summary["main_suite_counts"] = suite_counts
summary["real_runtime_windows"] = runtime_windows
toolchain = document("observation-controls-", "perf-source-toolchain/report.json")
assert toolchain["passed"] and toolchain["cleanup_verified"]
assert toolchain["scope"] == "TOOLCHAIN_SMOKE" and toolchain["live_perf_sampling"] is False
assert toolchain["causal_root_cause_verified"] is False
isolation = toolchain["container_isolation"]
assert isolation["readonly_rootfs"] and isolation["network_mode"] == "none"
assert isolation["user"] == "mini-drop" and isolation["cap_drop"] == ["ALL"]
assert isolation["memory_bytes"] == 128 * 1024**2 and isolation["nano_cpus"] == 500000000
assert len(isolation["bind_mounts"]) == 3 and all(row["rw"] is False for row in isolation["bind_mounts"])
verification = toolchain["verification"]
assert verification["scope"] == "TOOLCHAIN_SMOKE" and verification["passed"]
assert verification["live_perf_sampling"] is False and verification["constructed_fixture_samples"] == 1
assert verification["source_file"].endswith("main.cpp") and verification["source_line"] > 0
assert "TOOLCHAIN_SMOKE" in verification["constructed_perf_script"]
assert verification["collapsed_stderr"] == ""
for dependency in ("perf", "perl", "nm", "addr2line", "objdump"):
    assert verification["tools"][dependency].startswith("/")
toolchain_artifact = artifact("observation-controls-")
binary_pin = next(row for row in toolchain_artifact["files"] if row["path"] == "perf-source-toolchain/cpp-hotspot")
assert binary_pin["sha256"] == verification["binary_sha256"]
binary_bytes = (STAGE / "ci-artifacts-final" / toolchain_artifact["name"] / binary_pin["path"]).read_bytes()
assert binary_bytes[:4] == b"\x7fELF"
assert set(verification["debug_sections"]) >= {".debug_info", ".debug_line"}
assert all(section.encode() in binary_bytes for section in (".debug_info", ".debug_line"))
python_artifact = artifact("python-quality-")
testcases = ET.parse(STAGE / "ci-artifacts-final" / python_artifact["name"] / "python-all/junit.xml").findall(".//testcase")
parser_cases = [row for row in testcases if row.get("classname", "").endswith("test_perf_source_toolchain")]
assert len(parser_cases) == 26 and all(not any(row.find(state) is not None for state in ("failure", "error", "skipped")) for row in parser_cases)
summary["actual_perf_source_toolchain_smoke"] = toolchain
summary["actual_toolchain_parser_tests"] = {"passed": len(parser_cases), "failed": 0, "skipped": 0,
    "scope": "Parser test subset already included in Python suite; never added twice"}
summary["fixture_junit_boundary"] = "Auxiliary XML copied/generated by negative tests is preserved but never added to the actual CI test totals."
(STAGE / "ci-final-evidence-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
lines = ["# 官方 CI 证据复核", "", f"源码 `{summary['source_head']}`，CI [36907241773]({summary['url']}) **14/14 作业成功**。", "",
         "| 专项 | 实际通过 | 跳过 | 失败 |", "|---|---:|---:|---:|"]
for name, row in suite_counts.items():
    lines.append(f"| {name} | {row['passed']} | {row.get('skipped', 0)} | {row.get('failed', 0)} |")
lines += ["", "Python 临时测试目录中的负向/历史 XML 均保留，但没有加入当前执行总数。真实 Chromium 使用合成 API 数据，不能算云端诊断。", "",
          "| 真实镜像 | 同步写累计次数 | 写入字节 | 本窗次数 | 平均操作耗时 ms | 清理 |",
          "|---|---:|---:|---:|---:|---|"]
for row in runtime_windows:
    lines.append(f"| {row['runtime']} | {row['io_operations_total']} | {row['io_bytes_written_total']} | {row['io_window']['operations']} | {row['io_window']['average_latency_ms']:.6f} | 通过 |")
cpp = next(r for r in runtime_windows if r["runtime"] == "cpp")["lock_window"]
py = next(r for r in runtime_windows if r["runtime"] == "python")
lines += ["", f"C++ 锁窗新增 {cpp['lock_acquisitions_delta']} 次获取、{cpp['lock_contentions_delta']} 次竞争、{cpp['lock_wait_ms_delta']:.2f}ms 获取等待，平均 {cpp['average_wait_ms']:.6f}ms。",
          f"Python 共享配额 `{py['cgroup_window']['cpu_max']}`；本窗实际新增 {py['cgroup_window']['nr_throttled_delta']} 次节流、{py['cgroup_window']['throttled_usec_delta']}μs 节流；peer 增长 {py['peer_cpu_ticks_delta']} ticks。", "",
          "三镜像均在受限只读容器和 64MiB tmpfs 内完成真实同步 I/O，累计写入超过 64MiB、失败 0、原始首尾重新计算一致。这验证持续观测与撤销清理，不代表真实块设备瓶颈、AI 因果根因或一小时压测。", "",
          "| 官方产物 | 下载 SHA256 |", "|---|---|"]
for row in summary["artifacts"]:
    lines.append(f"| {row['name']} | `{row['archive_sha256']}` |")
proof = summary["ci_source_equivalence"]
lines += ["", "实际 Analyzer 依赖层在只读、无网络、非root、128MiB/0.5CPU 容器中通过真实 ELF 工具链校验：",
    f"`{verification['nm_symbol_row']}` → `{verification['source_file']}:{verification['source_line']}`；{verification['perf_version']}，Perl {verification['perl_version']}。",
    f"ELF SHA：`{verification['binary_sha256']}`。真实安装工具包含 perf、Perl、nm、addr2line、objdump；debug_info/debug_line 保留。",
    "Perl --srcline 折叠文本来自真实 ELF 的地址/符号/源码映射，但 PID、时间和 1 个样本是明确构造的格式输入；此项只算 TOOLCHAIN_SMOKE，不算现场 perf 或根因。",
    "26 项工具链 parser 正负测试实际通过，已包含在 Python 总数中。", "",
    f"发布源码 tree `{proof['source_tree']}` 与 CI merge `{proof['ci_merge_head']}` tree相同；官方 Git commit/parents 元数据另存。", "",
    f"六个 ZIP 的 SHA256 和大小均与 GitHub 官方 API digest/size 一致；{file_count} 个解包文件及十四份 job 日志再次核对 SHA256。", "",
    "原始 ZIP、解包文件、十四个官方 Job 日志及逐文件 SHA 已保存在 `ci-artifacts-final/`。前两轮成功证据与中间失败证据保留；最终同 Git tree 证明另存 `ci-final-source-equivalence.json`。"]
(STAGE / "ci-final-evidence-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"main_suite_counts": suite_counts, "runtime_windows": runtime_windows,
    "toolchain": verification, "parser_tests": summary["actual_toolchain_parser_tests"],
    "source_equivalence": summary["ci_source_equivalence"]}, ensure_ascii=False))
