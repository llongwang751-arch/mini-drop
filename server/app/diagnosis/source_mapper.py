"""Map sampled hot symbols to bounded Python, Go, Java, Ruby and C/C++ source locations.

Source is read only from administrator-configured ``MINI_DROP_SOURCE_ROOTS``.
Mappings are review hints. They never become root-cause evidence without a
runtime artifact and a falsification step.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Iterable


SUPPORTED_SOURCE_SUFFIXES = {
    ".py", ".go", ".java", ".rb", ".cc", ".cpp", ".cxx", ".c", ".h", ".hpp", ".hh",
}


def configured_source_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in os.getenv("MINI_DROP_SOURCE_ROOTS", "").split(os.pathsep):
        value = raw.strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots


def map_hot_functions(
    symbols: Iterable[str],
    *,
    roots: list[Path] | None = None,
    max_files: int = 300,
    max_file_bytes: int = 1_000_000,
) -> dict:
    """Return bounded source mappings for sampled symbols.

    Python is parsed with the standard-library AST. Go, Java, Ruby and C/C++
    use conservative definition scanners that index function bodies but ignore
    declarations and call sites.
    """

    source_roots = roots if roots is not None else configured_source_roots()
    normalized = [_normalize_symbol(item) for item in symbols]
    targets = {item for item in normalized if item}
    mappings: list[dict] = []
    scanned = 0
    scanned_by_language = {
        "python": 0,
        "go": 0,
        "java": 0,
        "ruby": 0,
        "cpp": 0,
    }

    for root in source_roots:
        safe_root = root.resolve()
        for path in safe_root.rglob("*"):
            if scanned >= max_files or not targets:
                break
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(safe_root):
                continue
            scanned += 1
            try:
                if resolved.stat().st_size > max_file_bytes:
                    continue
                source = resolved.read_text(encoding="utf-8")
                language = _language_for(resolved)
                nodes = _source_function_nodes(source, language, str(resolved))
            except (OSError, UnicodeError, SyntaxError):
                continue
            scanned_by_language[language] += 1

            for qualname, line_start, line_end, review_signals in nodes:
                simple_name = qualname.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
                matched = {
                    symbol for symbol in targets
                    if _symbol_matches(symbol, qualname, simple_name)
                }
                for symbol in matched:
                    mappings.append({
                        "symbol": symbol,
                        "language": language,
                        "source_root": str(safe_root),
                        "file": resolved.relative_to(safe_root).as_posix(),
                        "qualname": qualname,
                        "line_start": int(line_start),
                        "line_end": int(line_end),
                        "review_signals": review_signals,
                        "confidence": "exact_symbol",
                        "disclaimer": "静态源码映射只用于缩小人工复核范围，不等同于已验证根因。",
                    })
                    targets.discard(symbol)

    return {
        "configured_roots": len(source_roots),
        "scanned_files": scanned,
        "scanned_by_language": scanned_by_language,
        "mappings": mappings,
        "unresolved_symbols": sorted(targets),
    }


def _normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip()
    if not symbol or symbol.lower() in {"[unknown]", "unknown", "no-samples", "all"}:
        return ""
    symbol = symbol.split("+0x", 1)[0].strip()
    symbol = symbol.split("(", 1)[0].strip()
    return symbol


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".go":
        return "go"
    if suffix == ".java":
        return "java"
    if suffix == ".rb":
        return "ruby"
    return "cpp"


def _source_function_nodes(
    source: str,
    language: str,
    filename: str,
) -> list[tuple[str, int, int, list[str]]]:
    if language == "python":
        tree = ast.parse(source, filename=filename)
        return [
            (
                qualname,
                int(node.lineno),
                int(getattr(node, "end_lineno", node.lineno)),
                _review_signals(node),
            )
            for qualname, node in _function_nodes(tree)
        ]
    if language == "go":
        return _go_function_nodes(source)
    if language == "java":
        return _java_function_nodes(source)
    if language == "ruby":
        return _ruby_function_nodes(source)
    return _cpp_function_nodes(source)


def _function_nodes(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    result: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree)
    return result


_GO_FUNCTION = re.compile(
    r"(?m)^[ \t]*func[ \t]+"
    r"(?:\((?P<receiver>[^)]*)\)[ \t]+)?"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\([^)]*\)"
    r"(?:[ \t]*(?:\([^)]*\)|[A-Za-z_][\w.\[\]*]*))?[ \t]*\{"
)


def _go_function_nodes(source: str) -> list[tuple[str, int, int, list[str]]]:
    result: list[tuple[str, int, int, list[str]]] = []
    for match in _GO_FUNCTION.finditer(source):
        receiver = match.group("receiver") or ""
        receiver_type = receiver.split()[-1].lstrip("*").split("[", 1)[0] if receiver else ""
        name = match.group("name")
        qualname = f"{receiver_type}.{name}" if receiver_type else name
        line_start = source.count("\n", 0, match.start()) + 1
        body_start = source.find("{", match.start(), match.end())
        line_end, body = _body_extent(source, body_start)
        result.append((qualname, line_start, line_end, _native_review_signals(body, "go")))
    return result


_JAVA_CLASS = re.compile(
    r"(?m)^[ \t]*(?:(?:public|protected|private|abstract|static|final|sealed|non-sealed)"
    r"[ \t]+)*(?:class|interface|enum|record)[ \t]+(?P<name>[A-Za-z_$]\w*)"
    r"[^;{}]*\{"
)
_JAVA_METHOD = re.compile(
    r"(?m)^[ \t]*(?:(?:public|protected|private|abstract|static|final|synchronized|"
    r"native|strictfp|default)[ \t]+)*(?:<[^;{}>]+>[ \t]+)?"
    r"[A-Za-z_$][\w$<>,.?@\[\]]*[ \t]+(?P<name>[A-Za-z_$]\w*)[ \t]*"
    r"\([^;{}]*\)[ \t]*(?:throws[ \t]+[^;{}]+)?\{"
)


def _java_function_nodes(source: str) -> list[tuple[str, int, int, list[str]]]:
    classes: list[tuple[int, int, str]] = []
    for match in _JAVA_CLASS.finditer(source):
        opening_brace = source.find("{", match.start(), match.end())
        closing_brace = _matching_brace(source, opening_brace)
        classes.append((opening_brace, closing_brace, match.group("name")))

    result: list[tuple[str, int, int, list[str]]] = []
    for match in _JAVA_METHOD.finditer(source):
        body_start = source.find("{", match.start(), match.end())
        containing = [
            item for item in classes
            if item[0] < match.start() < item[1]
        ]
        containing.sort(key=lambda item: item[0])
        class_name = ".".join(item[2] for item in containing)
        method_name = match.group("name")
        qualname = f"{class_name}.{method_name}" if class_name else method_name
        line_start = source.count("\n", 0, match.start()) + 1
        line_end, body = _body_extent(source, body_start)
        result.append((qualname, line_start, line_end, _native_review_signals(body, "java")))
    return result


_RUBY_DEF = re.compile(
    r"(?m)^[ \t]*def[ \t]+(?P<name>(?:self\.)?[A-Za-z_]\w*[!?=]?)"
)
_RUBY_SCOPE = re.compile(
    r"(?m)^[ \t]*(?:class|module)[ \t]+(?P<name>[A-Z]\w*(?:::[A-Z]\w*)*)"
)
_RUBY_STATEMENT_BLOCK = re.compile(
    r"(?:^|;)[ \t]*(def|class|module|if|unless|case|while|until|for|begin)\b"
)
_RUBY_DO_OR_END = re.compile(r"\b(do|end)\b")


def _ruby_function_nodes(source: str) -> list[tuple[str, int, int, list[str]]]:
    lines = source.splitlines(keepends=True)

    scopes: list[tuple[int, int, str]] = []
    for match in _RUBY_SCOPE.finditer(source):
        start_line = source.count("\n", 0, match.start())
        end_line = _ruby_block_end(lines, start_line)
        scopes.append((start_line, end_line, match.group("name")))

    result: list[tuple[str, int, int, list[str]]] = []
    for match in _RUBY_DEF.finditer(source):
        start_line = source.count("\n", 0, match.start())
        end_line = _ruby_block_end(lines, start_line)
        containing = [
            item for item in scopes
            if item[0] < start_line <= item[1]
        ]
        containing.sort(key=lambda item: item[0])
        scope_name = ".".join(item[2].replace("::", ".") for item in containing)
        method_name = match.group("name").removeprefix("self.")
        qualname = f"{scope_name}.{method_name}" if scope_name else method_name
        body = "".join(lines[start_line:end_line + 1])
        result.append(
            (
                qualname,
                start_line + 1,
                end_line + 1,
                _native_review_signals(body, "ruby"),
            )
        )
    return result


def _ruby_block_end(lines: list[str], start_line: int) -> int:
    depth = 0
    for line_index in range(start_line, len(lines)):
        code = _strip_ruby_strings_and_comment(lines[line_index])
        tokens: list[tuple[int, str]] = [
            (match.start(1), match.group(1))
            for match in _RUBY_STATEMENT_BLOCK.finditer(code)
        ]
        tokens.extend(
            (match.start(1), match.group(1))
            for match in _RUBY_DO_OR_END.finditer(code)
        )
        for _, token in sorted(tokens):
            if token == "end":
                depth -= 1
                if depth == 0:
                    return line_index
            else:
                depth += 1
    return max(start_line, len(lines) - 1)


def _strip_ruby_strings_and_comment(line: str) -> str:
    result: list[str] = []
    quote = ""
    escaped = False
    for char in line:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            result.append(" ")
            continue
        if char == "#":
            break
        if char in {'"', "'"}:
            quote = char
            result.append(" ")
        else:
            result.append(char)
    return "".join(result)


_CPP_FUNCTION = re.compile(
    r"(?m)^[ \t]*"
    r"(?:template[ \t]*<[^;{}]+>[ \t]*)?"
    r"(?:[\w:<>,~*&\[\]]+[ \t]+)+"
    r"(?P<name>(?:[A-Za-z_]\w*::)*[~A-Za-z_]\w*)[ \t]*"
    r"\([^;{}]*\)[ \t]*"
    r"(?:const[ \t]*)?(?:noexcept(?:\([^)]*\))?[ \t]*)?"
    r"(?:->[^{]+)?\{"
)


def _cpp_function_nodes(source: str) -> list[tuple[str, int, int, list[str]]]:
    result: list[tuple[str, int, int, list[str]]] = []
    for match in _CPP_FUNCTION.finditer(source):
        qualname = match.group("name")
        if qualname in {"if", "for", "while", "switch", "catch"}:
            continue
        line_start = source.count("\n", 0, match.start()) + 1
        body_start = source.find("{", match.start(), match.end())
        line_end, body = _body_extent(source, body_start)
        result.append((qualname, line_start, line_end, _native_review_signals(body, "cpp")))
    return result


def _matching_brace(source: str, opening_brace: int) -> int:
    if opening_brace < 0:
        return len(source)
    depth = 0
    quote = ""
    escaped = False
    for index in range(opening_brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(source)


def _body_extent(source: str, opening_brace: int) -> tuple[int, str]:
    if opening_brace < 0:
        return 1, ""
    closing_brace = _matching_brace(source, opening_brace)
    body_end = min(closing_brace + 1, len(source))
    return (
        source.count("\n", 0, closing_brace) + 1,
        source[opening_brace:body_end],
    )


def _review_signals(node: ast.AST) -> list[str]:
    signals: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
            signals.add("loop")
        elif isinstance(child, ast.Await):
            signals.add("await")
        elif isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name.endswith((".sleep", ".wait", ".join", ".acquire")):
                signals.add("blocking_or_wait_call")
            if name.endswith((".execute", ".executemany", ".query")):
                signals.add("database_call")
            if name.endswith((".get", ".post", ".request", ".send", ".recv")):
                signals.add("network_or_io_call")
    return sorted(signals)


def _native_review_signals(body: str, language: str) -> list[str]:
    signals: set[str] = set()
    if re.search(r"\b(for|while)\s*(?:\(|\w)", body):
        signals.add("loop")
    if re.search(r"\b(sleep|usleep|nanosleep|Sleep)\s*\(", body):
        signals.add("blocking_or_wait_call")
    if re.search(r"\b(lock|Lock|mutex|Mutex|WaitGroup|condition_variable)\b", body):
        signals.add("lock_or_coordination")
    if re.search(r"\b(read|write|recv|send|open|Read|Write)\s*\(", body):
        signals.add("network_or_io_call")
    if language == "go" and re.search(r"\bgo\s+\w|\bchan\b|<-", body):
        signals.add("goroutine_or_channel")
    return sorted(signals)


def _symbol_matches(symbol: str, qualname: str, simple_name: str) -> bool:
    canonical_symbol = symbol.replace("·", ".").replace("$", ".")
    canonical_qualname = qualname.replace("::", ".")
    return (
        canonical_symbol == simple_name
        or canonical_symbol == qualname
        or canonical_symbol == canonical_qualname
        or canonical_symbol.endswith(f".{canonical_qualname}")
        or canonical_symbol.endswith(f"::{simple_name}")
        or canonical_symbol.endswith(f".{simple_name}")
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}".strip(".")
    return ""

