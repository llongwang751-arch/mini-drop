"""Fix verification must treat missing data as REJECTED, never as a verified fix."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from server.app.drop_insight import fix_verification as fv
from server.app.models import ArtifactModel, Base, FixVerificationModel


def hotspot(name, percent):
    return {"name": name, "percent": percent}


@pytest.fixture()
def sessions(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(fv, "new_session", factory)
    yield factory
    engine.dispose()


def artifact(task_id, artifact_type, meta):
    return ArtifactModel(task_id=task_id, artifact_type=artifact_type,
                         object_key=f"objects/{task_id}/{artifact_type}", meta_json=meta,
                         created_at=datetime.now(timezone.utc))


def test_missing_before_data_cannot_establish_a_baseline():
    result = fv.compare_before_after(None, [hotspot("worker", 10)])
    assert result["outcome"] == "REJECTED"
    assert "修复前" in result["reason"]


def test_missing_after_data_is_rejected_not_counted_as_hotspot_disappearance():
    for after in (None, []):
        result = fv.compare_before_after([hotspot("worker", 80)], after)
        assert result["outcome"] == "REJECTED"
        assert "修复后" in result["reason"]


def test_disappeared_hotspot_verifies_without_inventing_an_after_sample():
    result = fv.compare_before_after([hotspot("worker", 80)], [hotspot("other", 50)])
    assert result["outcome"] == "VERIFIED"
    assert "消失" in result["reason"]
    assert result["after_hotspot"] is None
    assert result["before_percent"] == 80


def test_relative_drop_boundary_at_threshold_verifies_and_just_above_rejects():
    at_boundary = fv.compare_before_after([hotspot("worker", 100)], [hotspot("worker", 70)])
    assert at_boundary["outcome"] == "VERIFIED"
    assert at_boundary["after_percent"] == 70.0
    just_above = fv.compare_before_after([hotspot("worker", 100)], [hotspot("worker", 70.1)])
    assert just_above["outcome"] == "REJECTED"


def test_custom_threshold_moves_the_verification_boundary():
    rejected = fv.compare_before_after([hotspot("w", 100)], [hotspot("w", 60)], threshold=0.5)
    assert rejected["outcome"] == "REJECTED"
    verified = fv.compare_before_after([hotspot("w", 100)], [hotspot("w", 50)], threshold=0.5)
    assert verified["outcome"] == "VERIFIED"


def test_non_dict_rows_are_ignored_and_hotspots_rank_by_percent():
    before = ["junk", None, {"no_name": True}, hotspot("a", 40)]
    after = [{"skipped": True}, hotspot("a", 25)]
    result = fv.compare_before_after(before, after)
    assert result["outcome"] == "VERIFIED"
    assert result["before_hotspot"] == hotspot("a", 40)


def test_percent_strings_are_coerced_like_numbers():
    result = fv.compare_before_after([hotspot("worker", "100")], [hotspot("worker", "20")])
    assert result["outcome"] == "VERIFIED"
    assert result["after_percent"] == 20.0


def test_overlap_without_a_requested_window_counts_as_overlap():
    start = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    assert fv._time_ranges_overlap(start, start + timedelta(minutes=5), {}) is True
    assert fv._time_ranges_overlap(start, start + timedelta(minutes=5), {"start": start}) is True


def test_overlap_accepts_iso_strings_in_the_requested_window():
    result = fv._time_ranges_overlap(
        datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc),
        {"start": "2026-09-26T10:30:00Z", "end": "2026-09-26T10:45:00Z"})
    assert result is True


def test_disjoint_and_touching_windows_do_not_overlap():
    window = (datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
              datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc))
    disjoint = {"start": "2026-09-26T12:00:00Z", "end": "2026-09-26T12:30:00Z"}
    touching = {"start": "2026-09-26T11:00:00Z", "end": "2026-09-26T11:30:00Z"}
    assert fv._time_ranges_overlap(*window, disjoint) is False
    assert fv._time_ranges_overlap(*window, touching) is False


def test_naive_window_datetimes_are_compared_as_utc():
    result = fv._time_ranges_overlap(
        datetime(2026, 9, 26, 10, 0), datetime(2026, 9, 26, 11, 0),
        {"start": datetime(2026, 9, 26, 10, 30, tzinfo=timezone.utc),
         "end": datetime(2026, 9, 26, 10, 45, tzinfo=timezone.utc)})
    assert result is True


def test_invalid_requested_datetimes_report_no_overlap_instead_of_crashing():
    window = (datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
              datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc))
    assert fv._time_ranges_overlap(*window, {"start": "garbage", "end": "2026-09-26T10:45:00Z"}) is False


def test_parse_datetime_handles_none_non_strings_and_invalid_values():
    assert fv._parse_datetime(None) is None
    assert fv._parse_datetime(7) == 7
    parsed = fv._parse_datetime("2026-09-26T10:00:00Z")
    assert parsed is not None and parsed.tzinfo is not None
    assert fv._parse_datetime("not-a-date") is None


def test_task_top_functions_reads_first_valid_top_json_artifact(sessions):
    with sessions.begin() as session:
        session.add(artifact("t1", "flamegraph", {"top_functions": [hotspot("x", 1)]}))
        session.add(artifact("t1", "top_json", {"top_functions": [hotspot("worker", 80)]}))
        session.add(artifact("t2", "top_json", {"unrelated": True}))
    assert fv._task_top_functions("t1") == [hotspot("worker", 80)]
    assert fv._task_top_functions("t2") == []
    assert fv._task_top_functions("missing") == []


def test_task_top_functions_skips_artifacts_without_a_top_list(sessions):
    with sessions.begin() as session:
        session.add(artifact("t3", "top_json", None))
        session.add(artifact("t3", "top_json", {"top_functions": [hotspot("w", 5)]}))
    assert fv._task_top_functions("t3") == [hotspot("w", 5)]


def test_verify_diagnosis_fix_persists_the_verified_comparison(sessions):
    with sessions.begin() as session:
        session.add(artifact("before-1", "top_json", {"top_functions": [hotspot("worker", 80)]}))
        session.add(artifact("after-1", "top_json", {"top_functions": [hotspot("worker", 20)]}))
    view = fv.verify_diagnosis_fix("diag-1", before_task_id="before-1", after_task_id="after-1",
                                   fix_summary="limit rerank candidates", created_by="tester")
    assert view["outcome"] == "VERIFIED"
    assert view["id"].startswith("fix_")
    assert view["diagnosis_id"] == "diag-1"
    assert view["fix_summary"] == "limit rerank candidates"
    assert view["comparison"]["before_percent"] == 80.0
    assert [row["outcome"] for row in fv.list_fix_verifications("diag-1")] == ["VERIFIED"]


def test_verify_diagnosis_fix_records_rejection_when_after_data_is_missing(sessions):
    with sessions.begin() as session:
        session.add(artifact("before-2", "top_json", {"top_functions": [hotspot("worker", 80)]}))
    view = fv.verify_diagnosis_fix("diag-2", before_task_id="before-2", after_task_id="after-missing")
    assert view["outcome"] == "REJECTED"
    stored = fv.list_fix_verifications("diag-2")[0]
    assert stored["comparison"]["reason"]
    assert stored["after_task_id"] == "after-missing"


def test_list_fix_verifications_orders_newest_first_and_respects_limit(sessions):
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        for index, created in enumerate([now - timedelta(minutes=2), now, now - timedelta(minutes=1)]):
            session.add(FixVerificationModel(
                id=f"fix_{index}", diagnosis_id="diag-3", before_task_id="b", after_task_id="a",
                outcome="VERIFIED", comparison_json={"index": index}, created_at=created))
    rows = fv.list_fix_verifications("diag-3")
    assert [row["comparison"]["index"] for row in rows] == [1, 2, 0]
    assert [row["comparison"]["index"] for row in fv.list_fix_verifications("diag-3", limit=2)] == [1, 2]
