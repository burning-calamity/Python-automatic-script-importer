"""Command-line interface to the scanner (GUI optional)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import autoscripts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autoscripts-cli",
        description="Discover local Python scripts and inspect their import dependencies.",
    )
    parser.add_argument("--path", action="append", type=Path,
                        help="directory to scan (repeat for multiple roots)")
    parser.add_argument("--no-recursive", action="store_true",
                        help="do not descend into subdirectories")
    parser.add_argument("--no-imports", action="store_true",
                        help="skip static import analysis")
    subs = parser.add_subparsers(dest="action")
    subs.add_parser("list", help="show registered script modules (default)")
    deps = subs.add_parser("imports", help="show imports in one script or all scripts")
    deps.add_argument("module", nargs="?", help="autoscripts submodule name")
    deps.add_argument("--recursive", action="store_true", help="follow local imported scripts")
    subs.add_parser("status", help="display scan statistics")
    export = subs.add_parser("export", help="save full JSON/CSV scan report")
    export.add_argument("file", type=Path)
    args = parser.parse_args(argv)

    try:
        options = {"recursive": not args.no_recursive, "analyze_imports": not args.no_imports}
        if args.path:
            options["paths"] = args.path
        autoscripts.configure(**options)
        if args.action in (None, "list"):
            for name in autoscripts.available():
                print(f"autoscripts.{name}")
        elif args.action == "imports":
            if args.module is not None:
                print(json.dumps(autoscripts.imports_for(args.module, recursive=args.recursive), indent=2))
            else:
                print(json.dumps(autoscripts.dependency_graph(), indent=2))
        elif args.action == "status":
            print(json.dumps(autoscripts.status(), indent=2))
        elif args.action == "export":
            print(autoscripts.export_report(args.file))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
