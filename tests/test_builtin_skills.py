from pathlib import Path

import pytest

from server.app.drop_insight import builtin_skills
from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.builtin_skills import (
    REQUIRED_SECTIONS,
    SKILL_INSTRUCTION_SCHEMA,
    load_repository_skill_definitions,
    load_repository_skill_instructions,
    seed_repository_skills,
)
from server.app.drop_insight.skill_evolution import list_skills
from server.app.models import DiagnosticSkillModel


def test_repository_skill_catalog_is_executable():
    definitions = load_repository_skill_definitions()
    assert len(definitions) == 13
    assert len({item["slug"] for item in definitions}) == 13
    python_skill = next(
        item for item in definitions if item["slug"] == "python-runtime-diagnosis"
    )
    assert python_skill["category"] == "PYTHON_RUNTIME"
    assert python_skill["probe_order"][0] == "start_pyspy_profile"
    go_skill = next(item for item in definitions if item["slug"] == "go-runtime-diagnosis")
    assert go_skill["category"] == "GO_RUNTIME"
    assert go_skill["probe_order"][0] == "collect_go_profile"
    queue_skill = next(
        item for item in definitions if item["slug"] == "queue-backlog-diagnosis"
    )
    assert queue_skill["category"] == "QUEUE_CONGESTION"
    cpp_skill = next(
        item for item in definitions if item["slug"] == "cpp-runtime-diagnosis"
    )
    assert cpp_skill["category"] == "CPU_HOTSPOT"
    assert cpp_skill["probe_order"][0] == "start_perf_profile"
    assert cpp_skill["required_query_terms_any"] == [
        "c++",
        "cpp",
        "cxx",
        "原生进程",
        "原生二进制",
    ]
    assert all(item["probe_order"] for item in definitions)
    assert all(len(item["content_sha256"]) == 64 for item in definitions)
    assert all(Path(item["source_path"]).as_posix().startswith("skills/") for item in definitions)
    assert python_skill["instructions"]["schema"] == SKILL_INSTRUCTION_SCHEMA
    assert python_skill["instructions"]["summary"].startswith("对 Python 进程")
    assert "# Python Runtime 循证诊断" in python_skill["instructions"]["body"]
    assert set(python_skill["instructions"]["sections"]) >= REQUIRED_SECTIONS
    assert python_skill["instructions"]["loaded_sections"] == list(
        python_skill["instructions"]["sections"]
    )
    assert python_skill["instructions"]["content_sha256"] == (
        python_skill["instructions"]["source_sha256"]
    )
    assert python_skill["instructions"]["load_mode"] == "FULL_SKILL_MD"
    assert python_skill["instructions"]["is_evidence"] is False


def test_repository_skills_seed_idempotently(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    first = seed_repository_skills()
    second = seed_repository_skills()
    assert first == {"created": 13, "unchanged": 0, "total": 13}
    assert second == {"created": 0, "unchanged": 13, "total": 13}
    skills = list_skills()
    assert len(skills) == 13
    assert all(item["status"] == "ACTIVE" for item in skills)
    assert all(item["source_diagnosis_ids"] == [] for item in skills)
    assert all("evidence_refs" not in item["strategy"] for item in skills)

    session = new_session()
    try:
        python_skill = (
            session.query(DiagnosticSkillModel)
            .filter(DiagnosticSkillModel.category == "PYTHON_RUNTIME")
            .one()
        )
        instructions = load_repository_skill_instructions(python_skill)
    finally:
        session.close()
    assert instructions is not None
    assert instructions["name"] == "python-runtime-diagnosis"
    assert instructions["trust"] == "REPOSITORY_REVIEWED"
    assert instructions["sections"]["安全边界"]
    reset_engine()


def test_repository_skill_instruction_loader_rejects_tampered_source(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    seed_repository_skills()
    session = new_session()
    skill = (
        session.query(DiagnosticSkillModel)
        .filter(DiagnosticSkillModel.category == "PYTHON_RUNTIME")
        .one()
    )
    session.expunge(skill)
    session.close()

    source = tmp_path / "skills" / "python-runtime-diagnosis" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("tampered after version seed", encoding="utf-8")
    monkeypatch.setattr(builtin_skills, "_repository_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="changed after version seeding"):
        load_repository_skill_instructions(skill)
    reset_engine()
