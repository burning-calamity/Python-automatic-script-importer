# AutoScripts 1.3 — Python script discovery and import explorer

**AutoScripts** discovers user-selected `.py` files as lazy `autoscripts.*` submodules and also **indexes the modules and submodules those scripts import**, including nested local dependencies. Import statements are extracted using Python's AST, **without executing scripts or installing third-party dependencies**. A desktop Tkinter explorer displays scanned files, registered modules, imports, import relationships, errors and recent changes.

Installation requires Python **3.10+** and no third-party runtime packages.

```bash
python -m pip install autoscripts-local
```

To work with the downloaded source ZIP before publishing:

```bash
python -m pip install -e .
```

Run the GUI by double-clicking `main.pyw` (Windows), or with `python main.py`, `python -m autoscripts`, or `autoscripts-monitor` after installation. Normal `import autoscripts` **does not open the GUI**. Tkinter must be available for the GUI; the library and CLI work without it.

## Import local Python files

For example, assume you are scanning a folder containing:

```text
my_project/
  program.py
  helper.py
  tools/
    encode.py
```

```python
import autoscripts

autoscripts.configure(paths=["my_project"], interval=5)
print(autoscripts.available())  # ['helper', 'program', 'tools.encode']

from autoscripts.tools import encode  # executes encode.py only when imported
```

The scanner starts as a daemon when `autoscripts` is imported, and checks for new/changed/deleted `.py` files every 10 seconds by default. It does not load the scripts during scanning.

## New: modules and submodules imported by scripts

Suppose `program.py` contains:

```python
import os.path
import requests.sessions
from collections import Counter
from tools import encode
from . import helper
```

The dependency explorer records the module names (`os.path`, `requests.sessions`, `collections`, `tools`), the imported member names (`Counter`, `encode`, `helper`) and their line numbers. When a corresponding file is found in your scan roots, such as `tools/encode.py` or `helper.py`, the dependency is linked to the *local discovered module*. Nested dotted names are preserved. A `from package import name` reference may be an attribute instead of a submodule; it is only marked as a confirmed local submodule when that file/package is discovered.

```python
import autoscripts

autoscripts.configure(paths="my_project")

# List all module names referenced by the scanned source files:
print(autoscripts.imported_modules())

# Detailed static import statements for program.py:
print(autoscripts.imports_for("program"))

# Includes dependencies in other discovered local scripts:
print(autoscripts.imports_for("program", recursive=True))

# All script -> imported names and relationships:
print(autoscripts.dependency_graph())

# Opt-in: actually import a module referenced by a script.
# This uses normal import semantics and may execute module code.
# os.path is usually already available in Python's standard library.
os_path = autoscripts.load_imported("os.path")
```

**Limitations:** AST inspection detects regular `import` and `from ... import ...` statements, including imports inside functions and conditional branches; these are *syntactic references*, not proof they executed. Dynamic imports (e.g., `importlib.import_module(variable)`) are not inferred. Imports of installed third-party packages are indexed by name, **not copied into the `autoscripts` namespace, downloaded, traversed, or automatically imported**. Only scanned local scripts are lazily importable as `autoscripts.*` submodules. Local relative import support depends on the original package layout and cannot be guaranteed under the synthetic namespace.

## GUI features

- **Scripts:** searchable/sortable file table, registration status, source preview, copy imports, open file location.
- **Imported modules:** live, filterable table of each script's imports with module paths, potential imported submodule/member, standard-library vs local vs external/unknown, and line numbers. For responsiveness, the GUI renders up to 7,000 matching rows per view (the API and JSON export retain the full index).
- **Recent changes:** additions, changes, removals and diagnostics for import syntax errors, unreadable files and naming conflicts.
- **Settings:** manage roots, recursion, scan interval, file limit, enable/disable AST analysis and maximum source bytes; changes stored in `~/.autoscripts_monitor.json`.
- **Export:** `.json` includes a full import graph and scan metadata; `.csv` contains script registry metadata only.

The default source parsing limit is 2 MB per script. Increase `max_source_bytes` to analyze larger Python files. Unparseable or oversized source files **can still be registered**; any import inspection problem is reported in `status()["import_diagnostics"]`.

```python
import autoscripts

autoscripts.configure(
    paths=["my_project", "another_folder"],
    interval=3,
    recursive=True,
    max_files=15000,
    analyze_imports=True,
    max_source_bytes=4_000_000,
)
print(autoscripts.status())
autoscripts.export_report("scan_report.json")
autoscripts.pause()
autoscripts.request_refresh()
autoscripts.resume()
```

## CLI (GUI not required)

```bash
autoscripts-cli --path my_project list
autoscripts-cli --path my_project imports program
autoscripts-cli --path my_project imports program --recursive
autoscripts-cli --path my_project status
autoscripts-cli --path my_project export report.json
```

## Packaging and publishing

This project has a standard `pyproject.toml`, MIT `LICENSE`, scripts/GUI entry points, automated tests, source distribution metadata and GitHub Actions examples.

See **[docs/PUBLISHING.md](docs/PUBLISHING.md)** for building a wheel and source distribution, testing on TestPyPI, and publishing to PyPI manually or via Trusted Publishing.

```bash
python -m pip install --upgrade build twine
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

**Security:** Scanning uses the executing user's file permissions. Scripts are not executed during metadata discovery or AST analysis, but Python will execute code normally if you later import a discovered script or call `load_imported()`. Only import code you trust. Script changes do not hot-reload modules already present in `sys.modules`. Hidden and symlinked `.py` files, virtual environments, `site-packages`, and common cache/build directories are excluded from scanning by default. For overlapping scan roots, the first matching module name wins. Root-local `__init__.py` files are skipped to avoid import-time side effects.

## Tests

```bash
python -m unittest discover -s tests -v
```
