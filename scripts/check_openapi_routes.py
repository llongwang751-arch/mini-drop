"""Fail when public Go/Python routes drift away from the OpenAPI contract."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _canonical(path: str) -> str:
    path = re.sub(r"\(\[\^/\]\+\)", "{}", path)
    path = re.sub(r"\([^()/]+(?:\|[^()/]+)+\)", "{}", path)
    return re.sub(r"\{[^}]+\}", "{}", path)


def _method_values(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Compare):
            continue
        operands = [candidate.left, *candidate.comparators]
        if not any(isinstance(value, ast.Name) and value.id == "method" for value in operands):
            continue
        values.update(
            value.value
            for value in operands
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value in HTTP_METHODS
        )
    return values


def _literal_path(node: ast.AST) -> str | None:
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Compare):
            continue
        operands = [candidate.left, *candidate.comparators]
        if not any(isinstance(value, ast.Name) and value.id == "path" for value in operands):
            continue
        for value in operands:
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith("/"):
                return value.value
    return None


def _uses_ids(node: ast.AST) -> bool:
    return any(isinstance(value, ast.Name) and value.id == "ids" for value in ast.walk(node))


def _python_routes() -> set[tuple[str, str]]:
    source = (ROOT / "server/app/diagnostic_ai_rpc.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    dispatch = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "dispatch"
    )
    routes: set[tuple[str, str]] = set()
    current_pattern: str | None = None
    for statement in dispatch.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ids" for target in statement.targets
        ):
            call = statement.value
            if isinstance(call, ast.Call) and len(call.args) >= 2:
                pattern = call.args[1]
                current_pattern = pattern.value if isinstance(pattern, ast.Constant) else None
            continue
        if not isinstance(statement, ast.If):
            continue
        route = _literal_path(statement.test)
        if route is None and _uses_ids(statement.test):
            route = current_pattern
        if route is None:
            continue
        for method in _method_values(statement):
            routes.add((method.lower(), _canonical("/api/v2" + route)))
    return routes


def _go_routes() -> set[tuple[str, str]]:
    route_pattern = re.compile(r'HandleFunc\("(GET|POST|PUT|PATCH|DELETE) ([^"]+)"')
    routes: set[tuple[str, str]] = set()
    for source_path in (ROOT / "apiserver/internal/httpapi").glob("*.go"):
        source = source_path.read_text(encoding="utf-8")
        routes.update(
            (method.lower(), _canonical(path))
            for method, path in route_pattern.findall(source)
        )
    return routes


def _openapi_routes() -> set[tuple[str, str]]:
    document = json.loads(
        (ROOT / "docs/contracts/openapi.v1.json").read_text(encoding="utf-8")
    )
    return {
        (method.lower(), _canonical(path))
        for path, operations in document["paths"].items()
        for method in operations
        if method.upper() in HTTP_METHODS
    }


def main() -> None:
    implemented = _go_routes() | _python_routes()
    documented = _openapi_routes()
    missing = sorted(implemented - documented)
    stale = sorted(documented - implemented)
    if missing or stale:
        messages = []
        if missing:
            messages.append("missing from OpenAPI: " + ", ".join(f"{method.upper()} {path}" for method, path in missing))
        if stale:
            messages.append("not implemented: " + ", ".join(f"{method.upper()} {path}" for method, path in stale))
        raise SystemExit("OpenAPI route drift detected\n" + "\n".join(messages))
    print(f"OpenAPI route check passed: {len(implemented)} method/path pairs")


if __name__ == "__main__":
    main()
