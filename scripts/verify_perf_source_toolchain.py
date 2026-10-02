"""Verify actual Analyzer dependencies against the deployed C++ demo ELF.

TOOLCHAIN_SMOKE only: nm/addr2line provide a real symbol and source position;
the one-sample perf-script text is constructed from those results. No perf
record, target process, diagnostic Evidence or causal acceptance is produced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(words, *, timeout=30):
    return subprocess.run(words, capture_output=True, text=True, timeout=timeout, check=True)


def parse_hotspot_symbol(nm_output):
    """Accept the real extern-C name or a complete demangled C++ signature."""
    matches = []
    for row in nm_output.splitlines():
        match = re.fullmatch(
            r"([0-9a-fA-F]+)\s+[Tt]\s+(cpp_cpu_hot_function(?:\([^()\r\n]*\))?)", row
        )
        if match:
            matches.append((match.group(1), match.group(2), row))
    if len(matches) != 1 or int(matches[0][0], 16) <= 0:
        raise RuntimeError("Expected one real cpp_cpu_hot_function text symbol")
    return matches[0]


def parse_source_mapping(mapped, expected_symbol):
    """Require addr2line's exact nm symbol and a measured positive main.cpp line."""
    lines = mapped.strip().splitlines()
    if len(lines) != 2 or lines[0] != expected_symbol:
        raise RuntimeError("addr2line did not resolve the actual hotspot function")
    source_location = re.sub(r" \(discriminator \d+\)$", "", lines[1])
    source = re.fullmatch(r"(.*/)?main\.cpp:([1-9][0-9]*)", source_location)
    if not source:
        raise RuntimeError("addr2line did not resolve main.cpp to a positive source line")
    return source_location, int(source.group(2))


def check_in_container(binary, stackcollapse):
    """Exercise installed Linux tools; keep measured mapping and synthetic text distinct."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Actual Linux toolchain container required")
    tools = {}
    for name in ("perf", "perl", "nm", "addr2line", "objdump"):
        executable = shutil.which(name)
        if not executable:
            raise RuntimeError("Missing Analyzer toolchain dependency: " + name)
        tools[name] = executable
    perf_version = command([tools["perf"], "--version"]).stdout.strip()
    if not perf_version.startswith("perf version "):
        raise RuntimeError("Installed perf did not report a valid version")
    perl_version = command([tools["perl"], "-e", "print $^V"]).stdout.strip()
    section_table = command([tools["objdump"], "-h", str(binary)]).stdout
    if not all(re.search(r"\s" + re.escape(section) + r"\s", section_table)
               for section in (".debug_info", ".debug_line")):
        raise RuntimeError("Actual C++ demo ELF lacks required debug sections")
    nm_output = command([tools["nm"], "--defined-only", "-n", "-C", str(binary)]).stdout
    address, symbol, nm_row = parse_hotspot_symbol(nm_output)
    mapped = command([tools["addr2line"], "-f", "-C", "-e", str(binary), "0x" + address]).stdout
    source_location, source_line = parse_source_mapping(mapped, symbol)
    # Constructed format fixture: the PC, symbol and location above come from
    # the real ELF. Timestamp/PID/count below are deliberately synthetic.
    profile_text = (
        "# TOOLCHAIN_SMOKE: constructed format fixture; NOT a live perf sample\n"
        "cpp-hotspot 1/1 1.000000: cycles:\n"
        f"    {address} {symbol} ({binary})\n"
        f"    {source_location}\n\n"
    )
    with tempfile.TemporaryDirectory(prefix="mini-drop-perf-source-") as directory:
        profile = Path(directory) / "constructed-perf-script.txt"
        profile.write_text(profile_text, encoding="utf-8")
        collapsed = command([tools["perl"], str(stackcollapse), "--srcline", str(profile)])
    expected_folded = f"cpp-hotspot;cpp_cpu_hot_function:{source_location} 1"
    if collapsed.stdout.strip() != expected_folded or collapsed.stderr.strip():
        raise RuntimeError("Actual stackcollapse --srcline lost the real source location or sample count")
    return {
        "schema": "mini-drop.perf-source-toolchain-smoke.v1", "scope": "TOOLCHAIN_SMOKE",
        "passed": True, "live_perf_sampling": False, "causal_root_cause_verified": False,
        "binary_sha256": sha256(binary), "stackcollapse_sha256": sha256(stackcollapse),
        "tools": tools, "perf_version": perf_version, "perl_version": perl_version,
        "debug_sections": [".debug_info", ".debug_line"], "nm_symbol_row": nm_row,
        "address": "0x" + address, "addr2line_stdout": mapped,
        "source_file": source_location.rsplit(":", 1)[0], "source_line": source_line,
        "constructed_perf_script": profile_text, "constructed_fixture_samples": 1,
        "collapsed_stdout": collapsed.stdout, "collapsed_stderr": collapsed.stderr,
        "boundary": "Actual ELF/toolchain mapping; timestamp, PID and one sample are constructed. No live diagnosis or RCA grade.",
    }


def run_owned_containers(args):
    """Copy from the actual demo image, then inspect and run one isolated toolchain."""
    args.output.mkdir(parents=True, exist_ok=False)
    binary = (args.output / "cpp-hotspot").resolve()
    source = Path(__file__).resolve()
    collapse = source.parent.parent / "analyzer/scripts/stackcollapse-perf.pl"
    suffix = uuid4().hex
    artifact_name = "mini-drop-perf-elf-" + suffix
    toolchain_name = "mini-drop-perf-toolchain-" + suffix
    owned = []
    result = {
        "schema": "mini-drop.perf-source-toolchain-control.v1", "scope": "TOOLCHAIN_SMOKE",
        "started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
        "live_perf_sampling": False, "causal_root_cause_verified": False,
        "runner_sha256": sha256(source), "cleanup_verified": False,
    }
    try:
        for key, image in (("cpp_image", args.cpp_image), ("toolchain_image", args.toolchain_image)):
            info = json.loads(command(["docker", "image", "inspect", image]).stdout)[0]
            if info["Os"] != "linux":
                raise RuntimeError("Linux image required")
            result[key] = {"name": image, "id": info["Id"], "os": info["Os"], "architecture": info["Architecture"]}
        command(["docker", "create", "--name", artifact_name, "--entrypoint", "/bin/true", args.cpp_image])
        owned.append(artifact_name)
        command(["docker", "cp", artifact_name + ":/usr/local/bin/cpp-hotspot", str(binary)])
        words = [
            "docker", "create", "--name", toolchain_name, "--read-only", "--network", "none",
            "--tmpfs", "/tmp:size=16m,mode=1777", "--memory", "128m", "--cpus", "0.5",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "32",
            "--user", "mini-drop", "--entrypoint", "python",
            "--mount", f"type=bind,src={binary},dst=/usr/local/bin/cpp-hotspot,readonly",
            "--mount", f"type=bind,src={source},dst=/verify_perf_source_toolchain.py,readonly",
            "--mount", f"type=bind,src={collapse},dst=/stackcollapse-perf.pl,readonly",
            args.toolchain_image, "/verify_perf_source_toolchain.py", "--in-container",
            "--binary", "/usr/local/bin/cpp-hotspot", "--stackcollapse", "/stackcollapse-perf.pl",
        ]
        command(words)
        owned.append(toolchain_name)
        inspected = json.loads(command(["docker", "inspect", toolchain_name]).stdout)[0]
        host = inspected["HostConfig"]
        binds = [mount for mount in inspected["Mounts"] if mount["Type"] == "bind"]
        if (host["ReadonlyRootfs"] is not True or host["NetworkMode"] != "none"
            or len(binds) != 3 or any(mount["RW"] for mount in binds)
            or inspected["Config"]["User"] != "mini-drop"
            or "no-new-privileges" not in " ".join(host["SecurityOpt"] or [])
            or "ALL" not in (host["CapDrop"] or [])):
            raise RuntimeError("Toolchain container isolation does not match the smoke contract")
        result["container_isolation"] = {
            "readonly_rootfs": host["ReadonlyRootfs"], "network_mode": host["NetworkMode"],
            "user": inspected["Config"]["User"], "cap_drop": host["CapDrop"],
            "security_opt": host["SecurityOpt"], "memory_bytes": host["Memory"],
            "nano_cpus": host["NanoCpus"], "pids_limit": host["PidsLimit"],
            "bind_mounts": [{"destination": item["Destination"], "rw": item["RW"]} for item in inspected["Mounts"]],
        }
        measured = subprocess.run(["docker", "start", "-a", toolchain_name], capture_output=True,
                                  text=True, timeout=90)
        (args.output / "toolchain.stdout.json").write_text(measured.stdout, encoding="utf-8")
        (args.output / "toolchain.stderr.log").write_text(measured.stderr, encoding="utf-8")
        state = json.loads(command(["docker", "inspect", toolchain_name]).stdout)[0]["State"]
        if measured.returncode or state["ExitCode"] or state["OOMKilled"]:
            raise RuntimeError("Actual toolchain smoke failed; see retained container stdout/stderr")
        result["verification"] = json.loads(measured.stdout)
        if (result["verification"].get("passed") is not True
            or result["verification"].get("scope") != "TOOLCHAIN_SMOKE"
            or result["verification"]["binary_sha256"] != sha256(binary)
            or result["verification"]["stackcollapse_sha256"] != sha256(collapse)):
            raise RuntimeError("Container proof differs from the actual bound ELF/helper")
        result["passed"] = True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        result.update(error_type=type(error).__name__, error=str(error))
    finally:
        cleanup_errors = []
        for name in reversed(owned):
            try:
                removed = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=25)
                if removed.returncode:
                    cleanup_errors.append({"name": name, "exit_code": removed.returncode})
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append({"name": name, "error_type": type(error).__name__})
        result["cleanup_verified"] = bool(owned) and not cleanup_errors
        if cleanup_errors:
            result["cleanup_errors"] = cleanup_errors
        result["passed"] = result["passed"] and result["cleanup_verified"]
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scope": result["scope"], "passed": result["passed"], "cleanup_verified": result["cleanup_verified"]}))
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-image")
    parser.add_argument("--cpp-image")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--in-container", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--stackcollapse", type=Path)
    args = parser.parse_args()
    if args.in_container:
        if not args.binary or not args.stackcollapse:
            parser.error("Container mode requires --binary and --stackcollapse")
        print(json.dumps(check_in_container(args.binary, args.stackcollapse)))
        return 0
    if not args.toolchain_image or not args.cpp_image or not args.output:
        parser.error("Require --toolchain-image, --cpp-image and a fresh --output")
    return run_owned_containers(args)


if __name__ == "__main__":
    raise SystemExit(main())
