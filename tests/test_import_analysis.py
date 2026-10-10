"""Static discovery, nested imports, release CLI and safety tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import autoscripts
from autoscripts.cli import main as cli_main


class ImportAnalysisTests(unittest.TestCase):
    def setUp(self):
        autoscripts.pause()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        autoscripts.configure(paths=self.root, interval=60, max_files=15000,
                              max_source_bytes=2_000_000, analyze_imports=True)

    def tearDown(self):
        autoscripts.resume()
        self.temp.cleanup()

    def test_import_graph_without_execution(self):
        marker = self.root / "executed.txt"
        (self.root / "target.py").write_text(
            "import os.path\nimport requests.sessions as rs\nfrom collections import Counter\n"
            "from tools import helper\n"
            f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "helper.py").write_text("import json\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertFalse(marker.exists())
        deps = autoscripts.imports_for("target")
        self.assertEqual(len(deps), 4)
        self.assertEqual(deps[0]["module"], "os.path")
        self.assertEqual(deps[0]["category"], "stdlib")
        self.assertEqual(deps[1]["module"], "requests.sessions")
        self.assertEqual(deps[2]["member"], "Counter")
        self.assertEqual(deps[3]["local_target"], "tools.helper")
        self.assertIn("json", [item["module"] for item in autoscripts.imports_for("target", recursive=True)])
        self.assertIn("tools.helper", autoscripts.imported_modules("target"))
        self.assertFalse(marker.exists())

    def test_root_level_relative_import(self):
        (self.root / "root.py").write_text("from . import sibling\n", encoding="utf-8")
        (self.root / "sibling.py").write_text("VALUE = 1\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertEqual(autoscripts.imports_for("root")[0]["local_target"], "sibling")

    def test_relative_import_resolution_and_cycles(self):
        d = self.root / "pkg"
        d.mkdir()
        (d / "a.py").write_text("from . import b\nfrom .sub import c\n", encoding="utf-8")
        (d / "b.py").write_text("from . import a\n", encoding="utf-8")
        (d / "sub").mkdir()
        (d / "sub" / "c.py").write_text("import math\n", encoding="utf-8")
        autoscripts.refresh()
        records = autoscripts.imports_for("pkg.a")
        self.assertEqual([r["local_target"] for r in records], ["pkg.b", "pkg.sub.c"])
        graph = autoscripts.imports_for("pkg.a", recursive=True)
        self.assertEqual(len(graph), 4)
        self.assertIn("math", autoscripts.imported_modules("pkg.a", recursive=True))

    def test_parser_syntax_error_registered_not_loaded(self):
        (self.root / "badsyntax.py").write_text("from example import (\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertIn("badsyntax", autoscripts.available())
        self.assertEqual(autoscripts.imports_for("badsyntax"), [])
        self.assertIn("SyntaxError", autoscripts.status()["import_diagnostics"]["badsyntax"])

    def test_changing_source_refreshes_imports(self):
        path = self.root / "switcher.py"
        path.write_text("import math\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertEqual(autoscripts.imported_modules("switcher"), ["math"])
        path.write_text("import statistics\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertEqual(autoscripts.imported_modules("switcher"), ["statistics"])

    def test_from_attribute_not_assumed_submodule(self):
        (self.root / "source.py").write_text("from math import sqrt\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertEqual(autoscripts.imported_modules("source"), ["math"])
        self.assertEqual(autoscripts.imports_for("source")[0]["submodule_candidate"], "math.sqrt")

    def test_toggle_analysis_and_source_limit(self):
        (self.root / "sample.py").write_text("import json\n", encoding="utf-8")
        autoscripts.configure(analyze_imports=False)
        self.assertEqual(autoscripts.imported_modules(), [])
        autoscripts.configure(analyze_imports=True, max_source_bytes=1024)
        self.assertIn("json", autoscripts.imported_modules())
        with self.assertRaises(ValueError):
            autoscripts.configure(max_source_bytes=100)
        huge = self.root / "huge.py"
        huge.write_text("import re\n" + ("#" * 1500), encoding="utf-8")
        autoscripts.refresh()
        self.assertIn("huge", autoscripts.available())
        self.assertEqual(autoscripts.imports_for("huge"), [])
        self.assertIn("skipped", autoscripts.status()["import_diagnostics"]["huge"])

    def test_export_includes_import_graph(self):
        (self.root / "source.py").write_text("import datetime\n", encoding="utf-8")
        autoscripts.refresh()
        report = self.root / "output.json"
        autoscripts.export_report(report)
        content = json.loads(report.read_text())
        self.assertEqual(content["imports"]["source"][0]["module"], "datetime")
        self.assertEqual(content["status"]["imported_module_count"], 1)

    def test_explicit_load_requires_indexed_name(self):
        (self.root / "source.py").write_text("import math\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertEqual(autoscripts.load_imported("math").sqrt(9), 3)
        with self.assertRaises(ValueError):
            autoscripts.load_imported("unregistered_dependency")

    def test_cli_list_and_imports(self):
        (self.root / "sample.py").write_text("import os\n", encoding="utf-8")
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli_main(["--path", str(self.root), "imports", "sample"]), 0)
        records = json.loads(output.getvalue())
        self.assertEqual(records[0]["module"], "os")

    def test_local_module_can_have_nested_name(self):
        (self.root / "a.py").write_text("import some.deep.layer\n", encoding="utf-8")
        autoscripts.refresh()
        self.assertIn("some.deep.layer", autoscripts.imported_modules())
        self.assertEqual(autoscripts.imports_for("a")[0]["category"], "external / unknown")


if __name__ == "__main__":
    unittest.main()
