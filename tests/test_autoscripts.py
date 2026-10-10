import importlib
from pathlib import Path
import tempfile
import unittest

import autoscripts


class AutoScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        autoscripts.configure(paths=self.root, interval=60)

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_without_running_code(self):
        script = self.root / 'side_effect_sample.py'
        marker = self.root / 'ran.txt'
        script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\nVALUE=123\n')
        autoscripts.refresh()
        self.assertIn('side_effect_sample', autoscripts.available())
        self.assertFalse(marker.exists())
        imported = importlib.import_module('autoscripts.side_effect_sample')
        self.assertEqual(imported.VALUE, 123)
        self.assertTrue(marker.exists())

    def test_nested_import(self):
        d = self.root / 'nested_case'
        d.mkdir()
        (d / 'math_example.py').write_text('def double(x): return x * 2\n')
        autoscripts.refresh()
        self.assertIn('nested_case.math_example', autoscripts.available())
        from autoscripts.nested_case import math_example
        self.assertEqual(math_example.double(4), 8)

    def test_new_files_discovered_on_attribute_access(self):
        (self.root / 'new_sample.py').write_text('NUMBER = 42\n')
        self.assertEqual(autoscripts.new_sample.NUMBER, 42)

    def test_ignored_venv(self):
        d = self.root / 'venv'
        d.mkdir()
        (d / 'ignored_sample.py').write_text('X = 1\n')
        autoscripts.refresh()
        self.assertNotIn('venv.ignored_sample', autoscripts.available())

    def test_normalization(self):
        (self.root / 'my-tool.py').write_text('ANSWER = 100\n')
        autoscripts.refresh()
        self.assertEqual(importlib.import_module('autoscripts.my_tool').ANSWER, 100)

    def test_scan_results_and_counts(self):
        (self.root / 'ok.py').write_text('VALUE = 1\n')
        (self.root / '__init__.py').write_text('\n')
        (self.root / '.hidden.py').write_text('\n')
        (self.root / 'dup.py').write_text('\n')
        d = self.root / 'dup'
        d.mkdir()
        (d / 'child.py').write_text('\n')
        self.assertEqual(autoscripts.refresh(), 2)
        results = autoscripts.scan_results()
        info = autoscripts.status()
        self.assertEqual(info['scanned_count'], 5)
        self.assertEqual(info['count'], 2)
        self.assertEqual(info['skipped_count'], 3)
        self.assertEqual(len(results), 5)
        self.assertEqual(
            {item['module'] for item in results if item['result'] == 'Registered'},
            {'ok', 'dup.child'},
        )
        self.assertTrue(any(item['reason'] == 'Module name conflict' for item in results))
        self.assertTrue(any(item['reason'] == 'Hidden file' for item in results))
        self.assertTrue(any(item['reason'] == 'Package initializer' for item in results))
        results[0]['module'] = 'modified'
        self.assertNotEqual(autoscripts.scan_results()[0]['module'], 'modified')

    def test_request_refresh_nonblocking(self):
        (self.root / 'test_polling.py').write_text('FLAG=True\n')
        generation = autoscripts.status()['scan_generation']
        autoscripts.request_refresh()
        import time
        deadline = time.monotonic() + 3
        while autoscripts.status()['scan_generation'] <= generation:
            if time.monotonic() >= deadline:
                self.fail('Background refresh did not run')
            time.sleep(0.02)
        self.assertIn('test_polling', autoscripts.available())

    def test_conflicts(self):
        (self.root / 'conflict.py').write_text('WHICH="file"\n')
        d = self.root / 'conflict'
        d.mkdir()
        (d / 'sub.py').write_text('WHICH="nested"\n')
        autoscripts.refresh()
        self.assertIn('conflict.sub', autoscripts.available())
        self.assertNotIn('conflict', autoscripts.available())
        self.assertTrue(autoscripts.status()['collisions'])


if __name__ == '__main__':
    unittest.main()


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        autoscripts.pause()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        autoscripts.configure(paths=self.root, interval=60)

    def tearDown(self):
        autoscripts.resume()
        self.tmp.cleanup()

    def test_activity_add_edit_remove(self):
        import time
        name = 'activity_unique_sample'
        file = self.root / (name + '.py')
        file.write_text('VERSION = 1\n')
        autoscripts.refresh()
        self.assertEqual(autoscripts.status()['changes']['added'], 1)
        self.assertEqual(autoscripts.change_history(1)[0]['action'], 'Added')
        file.write_text('VERSION = 2000\n')
        autoscripts.refresh()
        self.assertEqual(autoscripts.status()['changes']['modified'], 1)
        file.unlink()
        autoscripts.refresh()
        self.assertEqual(autoscripts.status()['changes']['removed'], 1)
        self.assertEqual([x['action'] for x in autoscripts.change_history(3)],
                         ['Removed', 'Modified', 'Added'])

    def test_csv_json_export(self):
        import json
        import csv
        (self.root / 'report_sample.py').write_text('X=1\n')
        autoscripts.refresh()
        json_path = self.root / 'report.json'
        csv_path = self.root / 'report.csv'
        self.assertEqual(autoscripts.export_report(json_path), json_path)
        self.assertEqual(autoscripts.export_report(csv_path), csv_path)
        report = json.loads(json_path.read_text(encoding='utf-8'))
        self.assertEqual(report['status']['count'], 1)
        self.assertEqual(report['files'][0]['module'], 'report_sample')
        with csv_path.open(newline='', encoding='utf-8') as f:
            data = list(csv.DictReader(f))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['result'], 'Registered')
        with self.assertRaises(ValueError):
            autoscripts.export_report(self.root / 'unexpected.txt')

    def test_reserved_builtin_not_shadowed(self):
        (self.root / 'gui.py').write_text('XXX = True')
        (self.root / 'status.py').write_text('XXX = True')
        (self.root / 'gui').mkdir()
        (self.root / 'gui' / 'helper.py').write_text('X=1')
        autoscripts.refresh()
        self.assertIn('script_gui.helper', autoscripts.available())
        self.assertIn('script_status', autoscripts.available())
        self.assertNotIn('gui', autoscripts.available())
        self.assertTrue(callable(autoscripts.status))
        gui = importlib.import_module("autoscripts.gui")
        self.assertTrue(hasattr(gui, "AutoScriptsMonitor"))

    def test_duplicate_root_removed(self):
        (self.root / 'unique123.py').write_text('X=1')
        self.assertEqual(autoscripts.configure(paths=[self.root, self.root]), 1)
        self.assertEqual(autoscripts.status()['scanned_count'], 1)

    def test_paused_manual_refresh_and_resume(self):
        import time
        autoscripts.pause()
        self.assertTrue(autoscripts.status()['paused'])
        (self.root / 'paused_script.py').write_text('READY=1')
        generation = autoscripts.status()['scan_generation']
        autoscripts.request_refresh()
        deadline = time.monotonic() + 4
        while autoscripts.status()['scan_generation'] == generation:
            if time.monotonic() > deadline:
                self.fail('manual scan while paused did not run')
            time.sleep(.025)
        self.assertIn('paused_script', autoscripts.available())
        autoscripts.resume()
        self.assertFalse(autoscripts.status()['paused'])

    def test_synthetic_modules_not_loaded_by_scan(self):
        name = 'lazy_must_not_load_99001011'
        (self.root / (name + '.py')).write_text('raise RuntimeError("must not run")')
        autoscripts.refresh()
        self.assertEqual(autoscripts.module_info(name)['loaded'], False)
        self.assertIsNone(autoscripts.module_info('nonexistent_module'))

    def test_validation_and_recovery_from_scan_limit(self):
        (self.root / 'a.py').write_text('X=1')
        (self.root / 'b.py').write_text('X=2')
        with self.assertRaises(RuntimeError):
            autoscripts.configure(paths=self.root, max_files=1)
        self.assertIsNotNone(autoscripts.status()['last_error'])
        autoscripts.configure(max_files=15000)
        self.assertIsNone(autoscripts.status()['last_error'])
        self.assertEqual(autoscripts.status()['count'], 2)
        with self.assertRaises(ValueError):
            autoscripts.configure(interval=0.1)
        with self.assertRaises(ValueError):
            autoscripts.configure(max_files=0)
        with self.assertRaises(ValueError):
            autoscripts.change_history(-2)
