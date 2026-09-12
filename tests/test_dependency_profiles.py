"""Keep extraction-only imports from breaking lightweight weekly jobs."""
import builtins
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "test_article_database_recovery.py"
EXTRACTION_MODULES = ("bs4", "trafilatura", "tldextract", "pypdf")


class CollectorImportReached(Exception):
    """The dependency check allowed the actual collector import."""


class DependencyProfileTests(unittest.TestCase):
    def probe_import(self, missing=(), required=False, collector_error=None):
        # Exercise the actual test module, not a duplicate of its guard.
        # Intercept collector imports; these profile checks need only stdlib.
        real_import = builtins.__import__

        def find_spec(name, *_args, **_kwargs):
            if name in missing:
                return None
            return importlib.machinery.ModuleSpec(name, loader=None)

        def checked_import(name, *args, **kwargs):
            if name in {
                "brief_backfill_article_content",
                "brief_backfill_article_content_resumable",
            }:
                if collector_error is not None:
                    raise collector_error
                raise CollectorImportReached(name)
            return real_import(name, *args, **kwargs)

        with patch.dict(os.environ, {
            "AIEO_REQUIRE_EXTRACTION_TESTS": "1" if required else "0"
        }), patch.object(sys, "path", list(sys.path)), \
             patch.object(importlib.util, "find_spec", side_effect=find_spec), \
             patch.object(builtins, "__import__", side_effect=checked_import):
            runpy.run_path(str(TARGET), run_name="__dependency_profile_probe__")

    def test_lightweight_profile_skips_before_importing_collector(self):
        for module in EXTRACTION_MODULES:
            with self.subTest(missing=module):
                with self.assertRaisesRegex(unittest.SkipTest, module):
                    self.probe_import(missing={module})

    def test_required_extraction_profile_fails_instead_of_skipping(self):
        for module in EXTRACTION_MODULES:
            with self.subTest(missing=module):
                with self.assertRaisesRegex(RuntimeError, module):
                    self.probe_import(missing={module}, required=True)

    def test_complete_profile_reaches_collector_imports(self):
        for required in (False, True):
            with self.subTest(required=required):
                with self.assertRaises(CollectorImportReached):
                    self.probe_import(required=required)

    def test_unrelated_collector_import_error_is_not_hidden(self):
        error = ModuleNotFoundError("internal collector regression")
        with self.assertRaises(ModuleNotFoundError) as raised:
            self.probe_import(collector_error=error)
        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
