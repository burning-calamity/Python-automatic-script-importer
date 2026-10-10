"""Read Python import statements statically, without executing scanned code.

This module deliberately does not call importlib.find_spec on third-party
packages: resolving dotted names that way can execute parent packages.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import tokenize

_STDLIB = frozenset(getattr(sys, "stdlib_module_names", sys.builtin_module_names))


def parse_source(path: Path, max_source_bytes: int) -> tuple[list[dict[str, object]], str]:
    """Return individual import occurrences and an optional parsing diagnostic."""
    try:
        if path.stat().st_size > max_source_bytes:
            return [], f"Import analysis skipped: file exceeds {max_source_bytes:,} bytes"
        # tokenize.open honors the encoding declaration (PEP 263).
        with tokenize.open(path) as file:
            source = file.read()
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError) as exc:
        return [], f"{type(exc).__name__}: {exc}"

    records: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.append({"kind": "import", "module": alias.name,
                                "member": "", "level": 0, "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                records.append({"kind": "from", "module": node.module or "",
                                "member": alias.name, "level": node.level,
                                "line": node.lineno})
    records.sort(key=lambda item: int(item["line"]))
    return records, ""


def _resolve_from(source_name: str, name: str, level: int) -> str | None:
    """Resolve a relative import against an autoscripts synthetic package."""
    if not level:
        return name
    parents = source_name.split(".")[:-1]
    if level > len(parents) + 1:
        return None
    prefix = parents[:len(parents) - level + 1]
    return ".".join(prefix + ([name] if name else []))


def classify_imports(
    module_name: str,
    raw: list[dict[str, object]],
    local_modules: set[str],
    local_packages: set[str],
) -> list[dict[str, object]]:
    """Annotate imports with submodule candidates and local graph edges.

    'from pkg import foo' names a symbol, which *might* be a submodule;
    only confirmed discovered local modules are treated as such.
    """
    out: list[dict[str, object]] = []
    local = local_modules | local_packages
    for record in raw:
        kind = str(record["kind"])
        level = int(record["level"])
        base = _resolve_from(module_name, str(record["module"]), level)
        member = str(record["member"])
        candidate = ""
        resolved_local = ""
        if kind == "from" and member != "*" and base is not None:
            candidate = f"{base}.{member}" if base else member
        if base is None:
            category = "unresolved relative"
            normalized = ("." * level) + str(record["module"])
        else:
            normalized = base
            for possible in (candidate, base):
                if possible in local:
                    resolved_local = possible
                    break
            # A dotted import can refer to a discovered namespace package.
            if not resolved_local and kind == "import":
                if normalized in local:
                    resolved_local = normalized
            if resolved_local:
                category = "local"
            elif base.split(".", 1)[0] in _STDLIB:
                category = "stdlib"
            elif base:
                category = "external / unknown"
            else:
                category = "unresolved"
        out.append({
            "source": module_name,
            "kind": kind,
            "module": normalized,
            "member": member,
            "submodule_candidate": candidate,
            "level": level,
            "line": int(record["line"]),
            "category": category,
            "local_target": resolved_local,
        })
    return out
