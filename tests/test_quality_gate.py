"""Guard the test infrastructure against false green results and lost evidence."""
from copy import deepcopy
import json

import pytest

from scripts import run_quality_gate as gate


def junit(tmp_path, body):
    path = tmp_path / "junit.xml"
    path.write_text(f'<testsuites><testsuite tests="999">{body}</testsuite></testsuites>', encoding="utf-8")
    return path


def test_junit_uses_executed_cases_and_preserves_failures(tmp_path):
    path = junit(tmp_path, '<testcase classname="a" name="ok"/>'
                 '<testcase classname="b" name="failed"><failure message="broken"/></testcase>'
                 '<testcase classname="b" name="setup"><error message="setup"/></testcase>')
    counts = gate.read_junit(path)
    assert counts["total"] == 3
    assert counts["passed"] == 1
    assert counts["failed"] == 2
    assert counts["failures"] == ["b::failed", "b::setup"]
    assert gate.classify([0], counts) == "FAILED"


def test_skip_allowlist_requires_class_and_reason_and_is_never_plain_pass(tmp_path):
    path = junit(tmp_path, '<testcase name="ok"/><testcase classname="db" name="lock">'
                 '<skipped message="requires isolated database"/></testcase>')
    rule = {"class": "db", "reason": "requires isolated database"}
    counts = gate.read_junit(path, [rule])
    assert gate.classify([0], counts) == "PASSED_WITH_SKIPS"
    assert counts["skips"][0]["reason"] == rule["reason"]
    for allowed in ([], [{**rule, "class": "wrong"}], [{**rule, "reason": "another reason"}]):
        assert gate.classify([0], gate.read_junit(path, allowed)) == "FAILED"


def test_all_skipped_is_failed_even_if_every_skip_is_allowed(tmp_path):
    path = junit(tmp_path, '<testcase classname="db" name="lock"><skipped message="absent"/></testcase>')
    counts = gate.read_junit(path, [{"class": "db", "reason": "absent"}])
    assert gate.classify([0], counts) == "FAILED"


@pytest.mark.parametrize("body", ["", '<error message="collection failed"/>'])
def test_empty_or_collection_error_report_cannot_pass(tmp_path, body):
    with pytest.raises(ValueError):
        gate.read_junit(junit(tmp_path, body))


@pytest.mark.parametrize("codes,error", [([], None), ([0, 1], None), ([0], "timed out")])
def test_command_failure_overrides_report(codes, error):
    assert gate.classify(codes, {"passed": 8, "failed": 0, "skipped": 0, "unexpected_skips": []}, error) == "FAILED"


def test_go_package_failure_overrides_passed_tests(tmp_path):
    path = tmp_path / "go.log"
    events = [{"Action": "pass", "Package": "api", "Test": "TestOK"},
              {"Action": "fail", "Package": "api"}]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert gate.classify([0], gate.read_go_json(path)) == "FAILED"


def test_go_skips_are_visible_and_unknown_skips_fail(tmp_path):
    path = tmp_path / "go.log"
    events = [{"Action": "pass", "Package": "api", "Test": "TestOK"},
              {"Action": "skip", "Package": "api", "Test": "TestDatabase"}]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert gate.classify([0], gate.read_go_json(path, ["TestDatabase"])) == "PASSED_WITH_SKIPS"
    assert gate.classify([0], gate.read_go_json(path)) == "FAILED"


def test_later_go_pass_cannot_erase_earlier_failure(tmp_path):
    path = tmp_path / "go.log"
    path.write_text('\n'.join(json.dumps({"Package": "api", "Test": "TestFlaky", "Action": action})
                              for action in ("fail", "pass")), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        gate.read_go_json(path)


def test_incomplete_go_execution_cannot_pass(tmp_path):
    path = tmp_path / "go.log"
    events = [{"Package": "api", "Test": "TestDone", "Action": "pass"},
              {"Package": "api", "Test": "TestLost", "Action": "run"}]
    path.write_text('\n'.join(json.dumps(event) for event in events), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        gate.read_go_json(path)


def test_report_remains_running_until_finalized_and_tracks_unrun_risks():
    plan = {"risks": {"db": "并发", "ui": "界面"}}
    report = {"state": "RUNNING", "selected_suites": ["db", "ui"], "suites": [
        {"id": "db", "risks": ["db"], "status": "PASSED"}]}
    gate.summarize(report, plan)
    assert report["status"] == "RUNNING"
    assert report["risk_results"]["ui"]["statuses"] == ["NOT_RUN"]
    report["state"] = "COMPLETED"
    gate.summarize(report, plan)
    assert report["status"] == "FAILED"


def test_existing_evidence_directory_is_never_overwritten(tmp_path):
    sentinel = tmp_path / "report.json"
    sentinel.write_text("old evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        gate.main(["--output", str(tmp_path)])
    assert sentinel.read_text() == "old evidence"


def test_missing_executable_is_recorded_and_cannot_pass(tmp_path):
    spec = {"title": "missing", "risks": ["test-harness"], "cwd": ".", "timeout_seconds": 5,
            "commands": [["mini-drop-executable-that-does-not-exist"]], "report": "exit-code"}
    result = gate.run_suite("missing", spec, tmp_path)
    assert result["status"] == "FAILED"
    assert "FileNotFoundError" in result["error"]
    assert result["logs"] == ["missing/command-1.log"]


def test_successful_process_with_missing_test_report_fails(tmp_path):
    spec = {"title": "missing report", "risks": ["test-harness"], "cwd": ".", "timeout_seconds": 5,
            "commands": [["{python}", "-c", "print('not a test report')"]], "report": "junit"}
    result = gate.run_suite("missing-report", spec, tmp_path)
    assert result["returncodes"] == [0]
    assert result["status"] == "FAILED"


def test_timeout_leaves_log_and_fails(tmp_path):
    spec = {"title": "timeout", "risks": ["test-harness"], "cwd": ".", "timeout_seconds": 0.2,
            "commands": [["{python}", "-c", "import time; print('started', flush=True); time.sleep(20)"]],
            "report": "exit-code"}
    result = gate.run_suite("timeout", spec, tmp_path)
    assert result["status"] == "FAILED"
    assert "TimeoutExpired" in result["error"]
    assert (tmp_path / result["logs"][0]).exists()
    assert result["duration_seconds"] < 10


def test_plan_requires_real_test_paths_and_valid_risk_references(tmp_path):
    plan = gate.load_plan()
    assert set(plan["profiles"]) == {"smoke", "python", "local", "business", "browser", "stability"}
    invalid = deepcopy(plan)
    invalid["suites"]["critical-python"]["commands"][0].append("tests/nonexistent_quality_test.py")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="missing test"):
        gate.load_plan(path)


COVERAGE_WRITER = ("import json,sys;json.dump({'totals':{'percent_covered':70.0},"
                   "'files':{'server/app/drop_insight/event_store.py':{'summary':"
                   "{'percent_covered':float(sys.argv[2])}}}}, open(sys.argv[1],'w'))")


def critical_suite(percent, floor=80):
    return {"title": "critical", "risks": ["false-conclusion"], "cwd": ".", "timeout_seconds": 30,
            "commands": [["{python}", "-c", COVERAGE_WRITER, "{output}/coverage.json", str(percent)]],
            "report": "exit-code",
            "critical_coverage": {"modules": {"server/app/drop_insight/event_store.py": floor}}}


def test_critical_coverage_below_floor_cannot_pass(tmp_path):
    result = gate.run_suite("critical", critical_suite(50.0), tmp_path)
    assert result["status"] == "FAILED"
    assert "CRITICAL_COVERAGE_BELOW_FLOOR" in result["error"]
    assert result["critical_coverage"]["minimum"] == 50.0
    module = result["critical_coverage"]["modules"]["server/app/drop_insight/event_store.py"]
    assert module["observed"] is True and module["floor"] == 80
    assert result["critical_coverage"]["breaches"] == ["server/app/drop_insight/event_store.py"]


def test_critical_module_missing_from_report_is_never_a_pass(tmp_path):
    spec = critical_suite(50.0)
    spec["commands"][0][2] = ("import json,sys;json.dump({'totals':{'percent_covered':70.0},"
                              "'files':{}}, open(sys.argv[1],'w'))")
    result = gate.run_suite("critical", spec, tmp_path)
    assert result["status"] == "FAILED"
    assert result["critical_coverage"]["minimum"] == 0.0
    module = result["critical_coverage"]["modules"]["server/app/drop_insight/event_store.py"]
    assert module["observed"] is False
    assert result["critical_coverage"]["breaches"] == ["server/app/drop_insight/event_store.py"]


def test_critical_coverage_at_or_above_floor_records_gate_evidence(tmp_path):
    result = gate.run_suite("critical", critical_suite(95.5), tmp_path)
    assert result["status"] == "PASSED"
    assert result["critical_coverage"]["minimum"] == 95.5
    assert result["critical_coverage"]["mode"] == "GATED"
    assert result["critical_coverage"]["breaches"] == []


def test_critical_coverage_names_only_the_modules_that_lost_coverage(tmp_path):
    spec = critical_suite(95.5)
    spec["critical_coverage"]["modules"]["server/app/drop_insight/report_conclusion.py"] = 69
    spec["commands"][0][2] = ("import json,sys;json.dump({'totals':{'percent_covered':70.0},"
                              "'files':{'server/app/drop_insight/event_store.py':{'summary':{'percent_covered':95.5}},"
                              "'server/app/drop_insight/report_conclusion.py':{'summary':{'percent_covered':60.0}}}}, "
                              "open(sys.argv[1],'w'))")
    result = gate.run_suite("critical", spec, tmp_path)
    assert result["status"] == "FAILED"
    assert result["critical_coverage"]["breaches"] == ["server/app/drop_insight/report_conclusion.py"]
    assert result["critical_coverage"]["minimum"] == 60.0


@pytest.mark.parametrize("mutation", [
    {"modules": {}}, {"modules": []}, {"modules": None},
    {"modules": {"server/app/drop_insight/event_store.py": 101}},
    {"modules": {"server/app/drop_insight/event_store.py": -1}},
    {"modules": {"server/app/drop_insight/event_store.py": "80"}},
    {"modules": {"server/app/drop_insight/nonexistent_module.py": 80}},
])
def test_plan_rejects_invalid_critical_coverage_contract(tmp_path, mutation):
    plan = gate.load_plan()
    invalid = deepcopy(plan)
    invalid["suites"]["python-all"]["critical_coverage"].update(mutation)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError):
        gate.load_plan(path)


def test_html_escapes_logs_and_test_failure_content(tmp_path):
    report = {"profile": "smoke", "status": "FAILED", "run_id": "id", "started_at": "now",
              "risk_results": {}, "suites": [{"title": '<script>alert("x")</script>',
              "status": "FAILED", "duration_seconds": 1, "logs": ['x" onclick="alert(1)'], "counts": None}]}
    gate.write_reports(tmp_path, report)
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report


def test_source_edit_during_run_invalidates_otherwise_passing_result(tmp_path, monkeypatch):
    plan = {"scope": "test", "profiles": {"smoke": ["check"]}, "suites": {"check": {}}, "risks": {}}
    monkeypatch.setattr(gate, "load_plan", lambda: plan)
    fingerprints = iter([{"source_sha256": "before"}, {"source_sha256": "after"}])
    monkeypatch.setattr(gate, "provenance", lambda: next(fingerprints))
    monkeypatch.setattr(gate, "run_suite", lambda *args: {
        "id": "check", "title": "check", "risks": [], "logs": [], "duration_seconds": 0,
        "status": "PASSED", "counts": None,
    })
    output = tmp_path / "run"
    assert gate.main(["--output", str(output)]) == 1
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAILED"
    assert report["error"].startswith("SOURCE_CHANGED_DURING_RUN")
