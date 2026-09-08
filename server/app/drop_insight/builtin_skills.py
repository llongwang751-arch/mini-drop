"""Load the repository Skill catalog into the runtime database.

The Markdown files remain the human-reviewable source. ``skills/catalog.json``
contains only the executable routing metadata needed by the planner. A content
hash creates a new immutable version when either source changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
from server.app.generated.taskkind_contract import TASK_KINDS
from server.app.models import DiagnosticSkillModel

from .tools import TOOL_BY_NAME, TOOL_TO_COLLECTOR


REQUIRED_SECTIONS = {
    "目标",
    "输入契约",
    "适用与停止条件",
    "取证顺序",
    "结论门槛",
    "必需证据与反证",
    "安全边界",
    "输出契约",
    "回归门禁",
}

SKILL_INSTRUCTION_SCHEMA = "mini-drop.repository-skill-instructions.v1"
_MAX_SKILL_MARKDOWN_BYTES = 128 * 1024


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _frontmatter(markdown: str) -> dict[str, str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            values[key.strip()] = value.strip()
    if not values.get("name") or not values.get("description"):
        raise ValueError("SKILL.md frontmatter requires name and description")
    return values


def _instruction_payload(markdown: str, *, source_path: str) -> dict:
    """Compile one reviewed repository Skill into a bounded prompt payload.

    Repository Skills are trusted release artifacts, but they still cross the
    model context boundary.  Decode and normalize them once, require the
    documented section contract, and retain a source digest so activation can
    detect a file changed outside the versioned seed flow.
    """

    encoded = markdown.encode("utf-8")
    if len(encoded) > _MAX_SKILL_MARKDOWN_BYTES:
        raise ValueError(
            f"Skill document exceeds {_MAX_SKILL_MARKDOWN_BYTES} bytes: {source_path}"
        )
    if "\x00" in markdown:
        raise ValueError(f"Skill document contains a NUL byte: {source_path}")

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    meta = _frontmatter(normalized)
    lines = normalized.splitlines()
    frontmatter_end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if frontmatter_end is None:
        raise ValueError("SKILL.md frontmatter is not terminated")
    body = "\n".join(lines[frontmatter_end + 1 :]).strip()
    if not body:
        raise ValueError(f"Skill document has an empty body: {source_path}")

    section_lines: dict[str, list[str]] = {}
    current_section: str | None = None
    for line in body.splitlines():
        if line.startswith("## "):
            current_section = line[3:].strip()
            section_lines.setdefault(current_section, [])
            continue
        if current_section is not None:
            section_lines[current_section].append(line)
    sections = {
        name: "\n".join(values).strip()
        for name, values in section_lines.items()
    }
    missing = sorted(REQUIRED_SECTIONS - set(sections))
    if missing:
        raise ValueError(f"Skill {meta['name']} is missing sections: {missing}")
    empty = sorted(name for name in REQUIRED_SECTIONS if not sections.get(name))
    if empty:
        raise ValueError(f"Skill {meta['name']} has empty sections: {empty}")

    source_sha256 = hashlib.sha256(encoded).hexdigest()
    return {
        "schema": SKILL_INSTRUCTION_SCHEMA,
        "name": meta["name"],
        "summary": meta["description"],
        "body": body,
        "sections": sections,
        # Compatibility aliases used by the service/event boundary.  This is
        # the digest of the exact Markdown body source, not the catalog + body
        # version fingerprint stored on DiagnosticSkillModel.
        "content_sha256": source_sha256,
        "loaded_sections": list(sections),
        "source_path": source_path,
        "source_sha256": source_sha256,
        "load_mode": "FULL_SKILL_MD",
        "trust": "REPOSITORY_REVIEWED",
        "is_evidence": False,
        "execution_policy": "ROUTE_PRIOR_WITH_SERVER_GATES",
    }


def load_repository_skill_instructions(skill: DiagnosticSkillModel) -> dict | None:
    """Load and verify the complete Markdown for an activated built-in Skill.

    Learned database Skills have no repository document and therefore return
    ``None``.  A repository Skill is loaded only from ``skills/``, must retain
    its seeded SHA-256 digest, and is re-parsed through the same section
    contract used at startup.  The model never receives an unchecked path or
    mutable file body.
    """

    trigger = dict(skill.trigger_json or {})
    strategy = dict(skill.strategy_json or {})
    if trigger.get("repository_builtin") is not True:
        return None
    relative = str(strategy.get("source_path") or "").strip()
    expected_sha256 = str(strategy.get("source_sha256") or "").lower()
    if not relative or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("repository Skill is missing a verified source reference")

    repository = _repository_root().resolve()
    skills_root = (repository / "skills").resolve()
    source_path = (repository / relative).resolve()
    try:
        source_path.relative_to(skills_root)
    except ValueError as exc:
        raise ValueError("repository Skill source escapes the skills directory") from exc
    if source_path.name != "SKILL.md" or not source_path.is_file():
        raise ValueError("repository Skill source is not a readable SKILL.md")

    raw = source_path.read_bytes()
    if len(raw) > _MAX_SKILL_MARKDOWN_BYTES:
        raise ValueError("repository Skill source exceeds the prompt size limit")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("repository Skill source changed after version seeding")
    try:
        markdown = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("repository Skill source is not valid UTF-8") from exc
    payload = _instruction_payload(markdown, source_path=relative)
    expected_name = source_path.parent.name
    if payload["name"] != expected_name:
        raise ValueError("repository Skill source name no longer matches its directory")
    return payload


def load_repository_skill_definitions(root: Path | None = None) -> list[dict]:
    repository = root or _repository_root()
    catalog_path = repository / "skills" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "1.0":
        raise ValueError("unsupported repository Skill catalog version")
    task_kinds = {item["name"] for item in TASK_KINDS}
    definitions = []
    seen_slugs: set[str] = set()
    for item in catalog.get("skills") or []:
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen_slugs:
            raise ValueError(f"duplicate or empty Skill slug: {slug!r}")
        seen_slugs.add(slug)
        source_path = repository / "skills" / slug / "SKILL.md"
        markdown = source_path.read_bytes().decode("utf-8")
        relative_source_path = source_path.relative_to(repository).as_posix()
        instructions = _instruction_payload(
            markdown,
            source_path=relative_source_path,
        )
        meta = {
            "name": instructions["name"],
            "description": instructions["summary"],
        }
        if meta["name"] != slug:
            raise ValueError(f"Skill name disagrees with folder: {slug}")
        route = [str(value) for value in item.get("probe_order") or []]
        if not route:
            raise ValueError(f"Skill {slug} has an empty probe route")
        for tool_name in route:
            if tool_name not in TOOL_BY_NAME:
                raise ValueError(f"Skill {slug} references unknown tool {tool_name}")
            collector = TOOL_TO_COLLECTOR.get(tool_name)
            if collector not in task_kinds:
                raise ValueError(
                    f"Skill {slug} references tool without executable TaskKind: {tool_name}"
                )
        canonical = json.dumps(
            {
                "catalog": item,
                "instruction_schema": SKILL_INSTRUCTION_SCHEMA,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256((markdown + "\n" + canonical).encode("utf-8")).hexdigest()
        definitions.append(
            {
                **item,
                "name": meta["name"],
                "description": meta["description"],
                "content_sha256": digest,
                "source_path": relative_source_path,
                "source_sha256": instructions["source_sha256"],
                "instructions": instructions,
            }
        )
    return definitions


def seed_repository_skills(root: Path | None = None) -> dict[str, int]:
    """Install or version repository Skills; never rewrite learned Skills."""

    definitions = load_repository_skill_definitions(root)
    created = 0
    unchanged = 0
    session = new_session()
    try:
        timestamp = datetime.now(timezone.utc)
        for definition in definitions:
            family_key = f"repository:{definition['slug']}"
            latest = (
                session.query(DiagnosticSkillModel)
                .filter(DiagnosticSkillModel.family_key == family_key)
                .order_by(DiagnosticSkillModel.version.desc())
                .first()
            )
            latest_hash = (
                (latest.strategy_json or {}).get("content_sha256") if latest else None
            )
            if latest_hash == definition["content_sha256"]:
                unchanged += 1
                continue
            session.query(DiagnosticSkillModel).filter(
                DiagnosticSkillModel.family_key == family_key,
                DiagnosticSkillModel.status == "ACTIVE",
            ).update(
                {"status": "RETIRED", "updated_at": timestamp},
                synchronize_session=False,
            )
            version = (latest.version + 1) if latest else 1
            digest = definition["content_sha256"]
            session.add(
                DiagnosticSkillModel(
                    id=f"builtin_{definition['slug'].replace('-', '_')}_{digest[:12]}",
                    family_key=family_key,
                    category=definition["category"],
                    version=version,
                    status="ACTIVE",
                    source_diagnosis_ids_json=[],
                    trigger_json={
                        "environment": "*",
                        "service": "",
                        "source_query": definition["description"],
                        "query_terms": definition.get("query_terms") or [],
                        "required_query_terms_any": definition.get("required_query_terms_any") or [],
                        "repository_builtin": True,
                    },
                    strategy_json={
                        "probe_order": definition["probe_order"],
                        "minimum_evidence": 1,
                        "confidence_floor": 0.6,
                        "stop_rule": "VERIFIED_REPORT_OR_EXHAUSTED_SAFE_PROBES",
                        "refutation_rule": "COUNTER_EVIDENCE_OVERRIDES_ROUTE_PRIOR",
                        "content_sha256": digest,
                        "source_path": definition["source_path"],
                        "source_sha256": definition["source_sha256"],
                        "instruction_schema": SKILL_INSTRUCTION_SCHEMA,
                    },
                    gate_metrics_json={
                        "eligible": True,
                        "evaluation_mode": "CODE_REVIEWED_REPOSITORY_BUILTIN",
                        "requires_campaign_validation": True,
                    },
                    parent_skill_id=latest.id if latest else None,
                    created_by="system:repository-skill-loader",
                    created_at=timestamp,
                    updated_at=timestamp,
                    published_at=timestamp,
                )
            )
            created += 1
        session.commit()
    except IntegrityError:
        # Another worker may seed the same deterministic version during a
        # rolling start. The winning transaction is sufficient.
        session.rollback()
    finally:
        session.close()
    return {"created": created, "unchanged": unchanged, "total": len(definitions)}
