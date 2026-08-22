from agent.mini_drop_agent.platform_compat import (
    build_compatibility_report,
    detect_host_platform,
    parse_os_release,
)


TLINUX_RELEASES = {
    2: 'NAME="TencentOS Server"\nID=tlinux\nVERSION_ID="2.4"\nPRETTY_NAME="TencentOS Server 2.4"',
    3: 'NAME="TencentOS Server"\nID=tlinux\nVERSION_ID="3.2"\nPRETTY_NAME="TencentOS Server 3.2"',
    4: 'NAME="TencentOS Server"\nID=tencentos\nVERSION_ID="4.0"\nPRETTY_NAME="TencentOS Server 4"',
}


def _which_for(*commands: str):
    available = set(commands)
    return lambda command: f"/usr/bin/{command}" if command in available else None


def test_parse_os_release_handles_quotes_and_comments():
    assert parse_os_release('# comment\nID="tlinux"\nVERSION_ID=2.4\n')["ID"] == "tlinux"


def test_detects_tlinux_2_3_4_and_package_manager():
    for major, release in TLINUX_RELEASES.items():
        host = detect_host_platform(
            os_release_text=release,
            which=_which_for("yum" if major == 2 else "dnf"),
            kernel_release="5.4.241-tlinux",
            architecture="x86_64",
        )
        assert host.is_tlinux is True
        assert host.tlinux_generation == major
        assert host.package_manager == ("yum" if major == 2 else "dnf")


def test_report_filters_collectors_by_real_host_capability():
    existing = {
        "/proc/self/status",
        "/proc/self/smaps",
        "/sys/kernel/tracing/available_events",
        "/sys/kernel/btf/vmlinux",
    }
    report = build_compatibility_report(
        ["perf_cpu", "continuous_perf", "ebpf_io", "pyspy", "go_pprof", "memory_smaps", "sys_metrics"],
        os_release_text=TLINUX_RELEASES[4],
        which=_which_for("dnf", "perf", "bpftrace"),
        path_exists=lambda path: path in existing,
        kernel_release="6.6.90-tlinux4",
        architecture="aarch64",
    )
    assert report["supported_tlinux_generation"] is True
    assert set(report["available_collectors"]) == {
        "perf_cpu", "continuous_perf", "ebpf_io", "go_pprof", "memory_smaps", "sys_metrics"
    }
    assert report["unavailable_collectors"] == {"pyspy": "requires py-spy"}
    assert report["host"]["architecture"] == "aarch64"


def test_unknown_tlinux_generation_is_rejected_by_preflight_report():
    report = build_compatibility_report(
        ["go_pprof"],
        os_release_text='ID=tlinux\nVERSION_ID="5"',
        which=_which_for("dnf"),
        path_exists=lambda _: False,
    )
    assert report["supported_tlinux_generation"] is False
