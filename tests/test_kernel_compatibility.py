from server.app.kernel_compatibility import evaluate_kernel_support


def _by_name(report):
    return {item["collector"]: item for item in report["collectors"]}


def test_modern_cap_perfmon_supports_perf_without_sys_admin():
    report = evaluate_kernel_support(
        release="6.8.0-ubuntu", perf_event_paranoid=4,
        capabilities={"CAP_PERFMON"}, tools={"perf"}, same_uid=False,
        ptrace_scope=1,
        btf_available=False, gperftools_opt_in=False,
    )
    assert _by_name(report)["perf_cpu"]["status"] == "SUPPORTED"
    assert report["cap_perfmon_available_since_kernel_5_8"] is True


def test_restricted_host_falls_back_without_claiming_a_profile():
    report = evaluate_kernel_support(
        release="5.4.0", perf_event_paranoid=4,
        capabilities=set(), tools={"perf", "mini-drop-gperftools-bridge"}, same_uid=True,
        ptrace_scope=1,
        btf_available=False, gperftools_opt_in=False,
    )
    rows = _by_name(report)
    assert rows["perf_cpu"]["status"] == "UNAVAILABLE"
    assert rows["cpp_gperftools"]["status"] == "UNAVAILABLE"
    assert rows["sys_metrics"]["status"] == "SUPPORTED"


def test_gperftools_requires_explicit_app_opt_in():
    report = evaluate_kernel_support(
        release="6.1.0", perf_event_paranoid=3,
        capabilities=set(), tools={"mini-drop-gperftools-bridge"}, same_uid=True,
        ptrace_scope=1,
        btf_available=False, gperftools_opt_in=True,
    )
    assert _by_name(report)["cpp_gperftools"]["status"] == "SUPPORTED"


def test_pyspy_does_not_ignore_a_restrictive_ptrace_policy():
    report = evaluate_kernel_support(
        release="6.8.0", perf_event_paranoid=2,
        capabilities=set(), tools={"py-spy"}, same_uid=True,
        ptrace_scope=3,
        btf_available=True, gperftools_opt_in=False,
    )
    assert _by_name(report)["pyspy"]["status"] == "DEGRADED"
