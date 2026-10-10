"""Discover trusted local Python files as importable, lazily-loaded submodules.

Importing ``autoscripts`` starts one daemon background scanner. Scanning
reads metadata and parses source imports, without executing discovered scripts.
"""
from __future__ import annotations

from collections import deque
if __name__ == "__main__" and not __package__:
    # Direct execution of autoscripts/__init__.py also opens the monitor.
    from pathlib import Path as _PathForDirectRun
    import sys as _sys_for_direct_run
    _sys_for_direct_run.path.insert(0, str(_PathForDirectRun(__file__).resolve().parent.parent))
    from autoscripts.dependency_analysis import parse_source, classify_imports
else:
    from .dependency_analysis import parse_source, classify_imports
import csv
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import keyword
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import ModuleType
from typing import Iterable

__version__ = "1.3.0"
__all__ = [
    "available", "configure", "refresh", "request_refresh", "scan_results",
    "status", "stop", "pause", "resume", "change_history", "export_report",
    "module_info", "wait_until_ready", "imports_for", "imported_modules",
    "dependency_graph", "load_imported",
]

_PACKAGE_NAME = "autoscripts"
_PACKAGE_DIR = Path(__file__).resolve().parent
_IGNORE_DIRS = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    ".env", "node_modules", "build", "dist", "site-packages",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
    ".nox", ".gradle", ".next", ".ipynb_checkpoints", "__pypackages__",
})
_RESERVED = frozenset(set(__all__).union({"gui", "__main__", "__init__", "dependency_analysis", "cli"}))

_lock = threading.RLock()
_scan_lock = threading.Lock()
_wake_event = threading.Event()
_stop_event = threading.Event()
_ready_event = threading.Event()


def _validate_paths(paths: Iterable[os.PathLike[str] | str]) -> tuple[Path, ...]:
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        if path not in seen:
            found.append(path)
            seen.add(path)
    if not found:
        raise ValueError("At least one scan directory is required")
    return tuple(found)


def _default_paths() -> tuple[Path, ...]:
    paths = [Path.cwd()]
    if sys.path and sys.path[0]:
        paths.append(Path(sys.path[0]))
    return _validate_paths(paths)


_roots = _default_paths()
_interval = 10.0
_recursive = True
_max_files = 15000
_analyze_imports = True
_max_source_bytes = 2_000_000
_import_graph: dict[str, tuple[dict[str, object], ...]] = {}
_imported_names: frozenset[str] = frozenset()
_import_diagnostics: dict[str, str] = {}
_ast_cache: dict[str, tuple[tuple[str, int, int], list[dict[str, object]], str, int]] = {}
_modules: dict[str, Path] = {}
_packages: set[str] = set()
_fingerprints: dict[str, tuple[str, int, int]] = {}
_scan_entries: tuple[dict[str, str], ...] = ()
_collisions: tuple[str, ...] = ()
_errors: tuple[str, ...] = ()
_history: deque[dict[str, str]] = deque(maxlen=500)
_last_changes = {"added": 0, "removed": 0, "modified": 0}
_last_scan = 0.0
_last_duration = 0.0
_last_error: str | None = None
_scan_generation = 0
_scan_in_progress = False
_progress_files = 0
_current_directory = ""
_paused = False
_pending_refresh = False


def _clean_name(name: str) -> str:
    """Make a predictable ASCII Python identifier from a filename."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned) or "unnamed"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    return cleaned


def _collect_scripts(
    roots: tuple[Path, ...], recursive: bool, max_files: int
) -> tuple[dict[str, Path], set[str], tuple[str, ...],
           tuple[dict[str, str], ...], dict[str, tuple[str, int, int]], tuple[str, ...]]:
    candidates: list[tuple[str, Path, int]] = []
    entries: list[dict[str, str]] = []
    errors: list[str] = []
    seen_files: set[Path] = set()
    examined = 0

    def walk_error(error: OSError) -> None:
        errors.append(f"{error.filename or 'directory'}: {error.strerror or error}")

    for root in roots:
        for directory, subdirs, filenames in os.walk(root, followlinks=False, onerror=walk_error):
            here = Path(directory)
            if here == _PACKAGE_DIR:
                subdirs[:] = []
                continue
            if recursive:
                subdirs[:] = sorted(
                    d for d in subdirs if d not in _IGNORE_DIRS and not d.startswith(".")
                    and not (here / d).is_symlink()
                    and (here / d).resolve() != _PACKAGE_DIR
                )
            else:
                subdirs[:] = []
            with _lock:
                global _current_directory
                _current_directory = str(here)

            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                examined += 1
                if examined > max_files:
                    raise RuntimeError(f"Scan limit exceeded ({max_files} .py files); narrow the scan folders or increase max_files")
                path = here / filename
                if examined % 64 == 0:
                    with _lock:
                        global _progress_files
                        _progress_files = examined
                # If roots overlap, only register a source file once (first root wins).
                resolved = path.absolute()
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                entry = {"path": str(path), "module": "", "result": "Skipped",
                         "reason": "", "size": "", "modified": ""}
                entries.append(entry)
                index = len(entries) - 1
                if filename == "__init__.py":
                    entry["reason"] = "Package initializer"
                    continue
                if filename.startswith("."):
                    entry["reason"] = "Hidden file"
                    continue
                if path.is_symlink() or not path.is_file():
                    entry["reason"] = "Link or non-regular file"
                    continue
                try:
                    stat = path.stat()
                except OSError as exc:
                    entry["reason"] = f"Stat error: {exc}"
                    errors.append(f"{path}: {exc}")
                    continue
                entry["size"] = str(stat.st_size)
                entry["modified"] = str(stat.st_mtime_ns)
                rel = path.relative_to(root)
                components = [_clean_name(part) for part in rel.parts[:-1]]
                if components and components[0] in _RESERVED:
                    components[0] = "script_" + components[0]
                stem = _clean_name(path.stem)
                if not components and stem in _RESERVED:
                    stem = "script_" + stem
                components.append(stem)
                name = ".".join(components)
                entry["module"] = name
                candidates.append((name, path, index))

    packages: set[str] = set()
    for name, _, _ in candidates:
        parts = name.split(".")
        for depth in range(1, len(parts)):
            packages.add(".".join(parts[:depth]))

    modules: dict[str, Path] = {}
    fingerprints: dict[str, tuple[str, int, int]] = {}
    collisions: list[str] = []
    for name, path, index in candidates:
        entry = entries[index]
        if name in packages or name in modules:
            entry["reason"] = "Module name conflict"
            collisions.append(f"{name}: {path}")
            continue
        modules[name] = path
        fingerprints[name] = (str(path), int(entry["size"]), int(entry["modified"]))
        entry["result"] = "Registered"
        entry["reason"] = "Importable on demand"
    with _lock:
        _progress_files = examined
    return modules, packages, tuple(collisions), tuple(entries), fingerprints, tuple(errors)


def _index_imports(
    modules: dict[str, Path], packages: set[str],
    fingerprints: dict[str, tuple[str, int, int]],
    previous_cache: dict[str, tuple[tuple[str, int, int], list[dict[str, object]], str, int]],
    max_source_bytes: int,
) -> tuple[dict[str, tuple[dict[str, object], ...]], dict[str, str],
           dict[str, tuple[tuple[str, int, int], list[dict[str, object]], str, int]]]:
    """Index declared imports, reusing AST output when a file is unchanged."""
    indexed: dict[str, tuple[dict[str, object], ...]] = {}
    diagnostics: dict[str, str] = {}
    cache: dict[str, tuple[tuple[str, int, int], list[dict[str, object]], str, int]] = {}
    names = set(modules)
    for name, path in modules.items():
        fingerprint = fingerprints[name]
        # Cache size limit is included: changing the max requires a reparse.
        cache_key = str(path)
        old = previous_cache.get(cache_key)
        if old is not None and old[0] == fingerprint and old[3] == max_source_bytes:
            raw, diagnostic = old[1], old[2]
        else:
            raw, diagnostic = parse_source(path, max_source_bytes)
        cache[cache_key] = (fingerprint, raw, diagnostic, max_source_bytes)
        if diagnostic:
            diagnostics[name] = diagnostic
        indexed[name] = tuple(classify_imports(name, raw, names, packages))
    return indexed, diagnostics, cache


def refresh() -> int:
    """Run a synchronous scan. No discovered source files are executed."""
    global _modules, _packages, _fingerprints, _collisions, _scan_entries, _errors
    global _last_scan, _last_duration, _last_error, _scan_generation, _last_changes
    global _scan_in_progress, _progress_files, _current_directory
    global _import_graph, _import_diagnostics, _ast_cache, _imported_names
    with _scan_lock:
        started = time.perf_counter()
        with _lock:
            roots, recursive, max_files = _roots, _recursive, _max_files
            analyze, max_bytes = _analyze_imports, _max_source_bytes
            cached = _ast_cache.copy()
            _scan_in_progress = True
            _progress_files = 0
            _current_directory = ""
        try:
            modules, packages, collisions, entries, fingerprints, errors = _collect_scripts(
                roots, recursive, max_files
            )
            graph, diagnostics, next_cache = (
                _index_imports(modules, packages, fingerprints, cached, max_bytes)
                if analyze else ({}, {}, {})
            )
        except Exception as exc:
            with _lock:
                _last_error = f"{type(exc).__name__}: {exc}"
                _last_scan = time.time()
                _last_duration = time.perf_counter() - started
                _scan_generation += 1
                _scan_in_progress = False
                _current_directory = ""
            _ready_event.set()
            raise
        imported_names = frozenset(
            value for imports in graph.values() for item in imports
            for value in (str(item["module"]), str(item["local_target"]))
            if value and not value.startswith(".")
        )
        now = time.time()
        with _lock:
            old = _fingerprints
            added = fingerprints.keys() - old.keys()
            removed = old.keys() - fingerprints.keys()
            modified = {name for name in old.keys() & fingerprints.keys()
                        if old[name] != fingerprints[name]}
            for action, names, mapping in (("Added", added, fingerprints),
                                           ("Removed", removed, old),
                                           ("Modified", modified, fingerprints)):
                for name in sorted(names):
                    _history.append({"timestamp": str(now), "action": action,
                                     "module": name, "path": mapping[name][0]})
            _last_changes = {"added": len(added), "removed": len(removed),
                             "modified": len(modified)}
            _modules, _packages = modules, packages
            _fingerprints = fingerprints
            _import_graph, _import_diagnostics, _ast_cache = graph, diagnostics, next_cache
            _imported_names = imported_names
            _collisions, _scan_entries, _errors = collisions, entries, errors
            _last_scan = now
            _last_duration = time.perf_counter() - started
            _last_error = None
            _scan_generation += 1
            _scan_in_progress = False
            _progress_files = len(entries)
            _current_directory = ""
        _ready_event.set()
        return len(modules)


def configure(
    paths: Iterable[os.PathLike[str] | str] | os.PathLike[str] | str | None = None,
    *, interval: float | None = None, recursive: bool | None = None,
    max_files: int | None = None, analyze_imports: bool | None = None,
    max_source_bytes: int | None = None,
) -> int:
    """Set scan options and immediately rescan, preserving the import API."""
    global _roots, _interval, _recursive, _max_files, _analyze_imports, _max_source_bytes
    if interval is not None and (not 0.5 <= float(interval) <= 86400):
        raise ValueError("interval must be between 0.5 and 86400 seconds")
    if max_files is not None and (not isinstance(max_files, int) or max_files < 1):
        raise ValueError("max_files must be a positive integer")
    if max_source_bytes is not None and (
        not isinstance(max_source_bytes, int) or max_source_bytes < 1024
    ):
        raise ValueError("max_source_bytes must be an integer of at least 1024")
    validated = None
    if paths is not None:
        validated = _validate_paths(
            [paths] if isinstance(paths, (str, os.PathLike)) else paths
        )
    with _lock:
        if validated is not None:
            _roots = validated
        if interval is not None:
            _interval = float(interval)
        if recursive is not None:
            _recursive = bool(recursive)
        if max_files is not None:
            _max_files = max_files
        if analyze_imports is not None:
            _analyze_imports = bool(analyze_imports)
        if max_source_bytes is not None:
            _max_source_bytes = max_source_bytes
    count = refresh()
    return count


def request_refresh() -> None:
    """Schedule a nonblocking scan, including while periodic scans are paused."""
    global _pending_refresh
    if not _worker.is_alive():
        raise RuntimeError("Background scanner has been stopped")
    with _lock:
        _pending_refresh = True
    _wake_event.set()


def pause() -> None:
    """Suspend automatic rescans (an in-flight scan may still finish)."""
    global _paused
    with _lock:
        _paused = True
    _wake_event.set()


def resume() -> None:
    """Restart periodic rescanning and request an immediate scan."""
    global _paused, _pending_refresh
    if not _worker.is_alive():
        raise RuntimeError("Background scanner has been stopped")
    with _lock:
        _paused = False
        _pending_refresh = True
    _wake_event.set()


def available() -> list[str]:
    with _lock:
        return sorted(_modules)


def scan_results() -> list[dict[str, str]]:
    with _lock:
        return [entry.copy() for entry in _scan_entries]


def dependency_graph() -> dict[str, list[dict[str, object]]]:
    """Static imports for every registered script (without importing them)."""
    with _lock:
        return {name: [dict(entry) for entry in records]
                for name, records in _import_graph.items()}


def imports_for(name: str, *, recursive: bool = False) -> list[dict[str, object]]:
    """Return imports in a script, optionally following scanned local modules.

    Recursion follows only local files known to the scanner. Standard-library
    and installed third-party packages are recorded but never traversed.
    """
    name = name.removeprefix(_PACKAGE_NAME + ".")
    with _lock:
        if name not in _import_graph:
            return []
        if not recursive:
            return [dict(item) for item in _import_graph[name]]
        graph = {key: [dict(item) for item in value]
                 for key, value in _import_graph.items()}
    if name not in graph:
        return []
    if not recursive:
        return graph[name]
    seen: set[str] = set()
    pending = [name]
    found: list[dict[str, object]] = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for record in graph.get(current, []):
            found.append(record)
            target = str(record["local_target"])
            if target in graph and target not in seen:
                pending.append(target)
    return found


def imported_modules(name: str | None = None, *, recursive: bool = False) -> list[str]:
    """All statically referenced module paths; confirmed local children included.

    'from package import symbol' is NOT considered a module unless the
    corresponding local package.symbol was discovered as a script/package.
    """
    if name is None:
        graph = dependency_graph()
        records = (record for entries in graph.values() for record in entries)
    else:
        records = iter(imports_for(name, recursive=recursive))
    result: set[str] = set()
    for record in records:
        module = str(record["module"])
        if module and not module.startswith("."):
            result.add(module)
        target = str(record["local_target"])
        if target:
            result.add(target)
    return sorted(result)


def load_imported(name: str) -> ModuleType:
    """Explicitly import an indexed dependency, potentially executing its code.

    Dependencies are never loaded by scanning. This function only accepts
    a name referenced in scanned scripts; import is performed normally and
    can raise ImportError if the package is not installed.
    """
    with _lock:
        allowed = name in _imported_names
    if not allowed:
        raise ValueError(f"Not a discovered imported module: {name}")
    return importlib.import_module(name)


def change_history(limit: int = 500) -> list[dict[str, str]]:
    """Latest additions/removals/edits (newest first; last 500 retained)."""
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    with _lock:
        return [event.copy() for event in list(reversed(_history))[:limit]]


def module_info(name: str) -> dict[str, object] | None:
    """Return a submodule's source path and import state, or None."""
    if name.startswith(_PACKAGE_NAME + "."):
        name = name[len(_PACKAGE_NAME) + 1:]
    with _lock:
        path = _modules.get(name)
    if path is None:
        return None
    loaded_module = sys.modules.get(_PACKAGE_NAME + "." + name)
    loaded_from = getattr(loaded_module, "__file__", None)
    stale = bool(loaded_from and Path(loaded_from).resolve() != path.resolve())
    return {"name": name, "path": str(path), "loaded": loaded_module is not None,
            "loaded_from": loaded_from, "stale": stale}


def wait_until_ready(timeout: float | None = 5.0) -> bool:
    return _ready_event.wait(timeout)


def status() -> dict:
    with _lock:
        return {
            "paths": [str(root) for root in _roots],
            "interval": _interval, "recursive": _recursive, "max_files": _max_files,
            "analyze_imports": _analyze_imports, "max_source_bytes": _max_source_bytes,
            "imported_module_count": len(_imported_names),
            "import_occurrences": sum(map(len, _import_graph.values())),
            "import_diagnostics": _import_diagnostics.copy(),
            "count": len(_modules), "scanned_count": len(_scan_entries),
            "skipped_count": sum(x["result"] != "Registered" for x in _scan_entries),
            "loaded_count": sum((_PACKAGE_NAME + "." + name) in sys.modules for name in _modules),
            "scan_generation": _scan_generation,
            "running": _worker.is_alive(), "paused": _paused,
            "scanning": _scan_in_progress, "progress_files": _progress_files,
            "current_directory": _current_directory,
            "ready": _ready_event.is_set(), "last_scan_epoch": _last_scan,
            "last_duration_seconds": _last_duration, "last_error": _last_error,
            "collisions": list(_collisions), "errors": list(_errors),
            "changes": _last_changes.copy(),
        }


def export_report(path: os.PathLike[str] | str, format: str | None = None) -> Path:
    """Write the current scan report as JSON or CSV; returns the destination."""
    destination = Path(path).expanduser()
    fmt = (format or destination.suffix.lstrip(".")).lower()
    if fmt not in {"json", "csv"}:
        raise ValueError("Report format must be json or csv")
    snapshot = scan_results()
    if fmt == "json":
        payload = {"version": __version__, "status": status(), "files": snapshot,
                   "history": change_history(),
                   "imports": dependency_graph()}
        with destination.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    else:
        with destination.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["path", "module", "result", "reason", "size", "modified"])
            writer.writeheader()
            writer.writerows(snapshot)
    return destination


def stop() -> None:
    """Permanently stop periodic scanning in this interpreter."""
    _stop_event.set()
    _wake_event.set()
    if threading.current_thread() is not _worker:
        _worker.join(timeout=2.0)


def _known(name: str) -> bool:
    with _lock:
        return name in _modules or name in _packages


def _load_child(package: str, child: str) -> ModuleType:
    if child.startswith("__") or not child.isidentifier():
        raise AttributeError(child)
    full = package + "." + child
    name = full[len(_PACKAGE_NAME) + 1:]
    if not _known(name):
        refresh()
    if not _known(name):
        raise AttributeError(f"module {package!r} has no submodule {child!r}")
    return importlib.import_module(full)


def _children(package: str) -> list[str]:
    prefix = package[len(_PACKAGE_NAME):].lstrip(".")
    base = prefix + "." if prefix else ""
    with _lock:
        names = set(_modules).union(_packages)
    return sorted({name[len(base):].split(".", 1)[0]
                   for name in names if name.startswith(base)})


class _SyntheticPackageLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__path__ = []
        module.__getattr__ = lambda name: _load_child(module.__name__, name)
        module.__dir__ = lambda: sorted(set(module.__dict__).union(_children(module.__name__)))


class _AutoScriptsFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        prefix = _PACKAGE_NAME + "."
        if not fullname.startswith(prefix):
            return None
        # Built-in package source files must never be shadowed by scanned files.
        direct_child = fullname[len(prefix):]
        if "." not in direct_child and direct_child in {"gui", "__main__", "__init__", "dependency_analysis", "cli"}:
            return None
        relative = fullname[len(prefix):]
        with _lock:
            file = _modules.get(relative)
            is_package = relative in _packages
        if file is None and not is_package:
            refresh()
            with _lock:
                file = _modules.get(relative)
                is_package = relative in _packages
        if is_package:
            return importlib.machinery.ModuleSpec(fullname, _SyntheticPackageLoader(), is_package=True)
        if file is not None:
            return importlib.util.spec_from_file_location(fullname, file)
        return None


def __getattr__(name: str) -> ModuleType:
    return _load_child(_PACKAGE_NAME, name)


def __dir__() -> list[str]:
    return sorted(set(globals()).union(_children(_PACKAGE_NAME)))


def _watch() -> None:
    global _pending_refresh
    while not _stop_event.is_set():
        with _lock:
            should_scan = not _paused or _pending_refresh
            _pending_refresh = False
        if should_scan:
            try:
                refresh()
            except Exception:
                # Errors are exposed by status() without terminating the daemon.
                pass
        with _lock:
            delay = None if _paused else _interval
        _wake_event.wait(delay)
        _wake_event.clear()


if __name__ == "__main__":
    sys.path.insert(0, str(_PACKAGE_DIR.parent))
    from autoscripts.gui import main as _run_gui
    _run_gui()
else:
    _finder = _AutoScriptsFinder()
    sys.meta_path.insert(0, _finder)
    _worker = threading.Thread(target=_watch, name="autoscripts-scanner", daemon=True)
    _worker.start()
