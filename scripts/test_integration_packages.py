"""只使用导入的合成源码树，不读取真实 CRM/Saber 代码或配置。"""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
KINDS = ("crm-python", "saber-python")


def load(kind):
    path = ROOT / "integrations" / kind / "package.py"
    spec = importlib.util.spec_from_file_location(kind.replace("-", "_") + "_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntegrationPackageTests(unittest.TestCase):
    def test_help_does_not_require_business_source(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "integrations" / kind / "package.py"), "--help"],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("--source", result.stdout)
                self.assertIn("--output", result.stdout)

    def test_missing_source_shows_required_explicit_paths(self):
        for kind in KINDS:
            with self.subTest(kind=kind), contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as raised:
                    load(kind).main([])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--source", error.getvalue())
                self.assertIn("--output", error.getvalue())
                self.assertIn("required", error.getvalue())

    def test_invalid_nested_and_nonempty_paths_rejected_before_writes(self):
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source, existing = base / "source", base / "existing"
                source.mkdir()
                existing.mkdir()
                (existing / "retained.txt").write_text("Imported stale fixture", encoding="utf-8")
                for actual_source, output in (
                    (base / "missing", base / "unused"),
                    (source, source / "nested-export"),
                    (source, existing),
                ):
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            load(kind).main(["--source", str(actual_source), "--output", str(output)])
                        self.assertEqual(raised.exception.code, 2)
                self.assertFalse((source / "nested-export").exists())
                self.assertFalse((base / "unused").exists())
                self.assertEqual((existing / "retained.txt").read_text(), "Imported stale fixture")

    def test_fake_export_keeps_source_unchanged_and_allowlist_exact(self):
        for kind in KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base, source = Path(directory), Path(directory) / "source"
                source.mkdir()
                if kind == "crm-python":
                    selected = {
                        "crm/services/brand_palette.py": "# Imported source fixture\ndef palette(): return []\n",
                        "crm/data/example.json": '{"fixture":true}\n',
                        "crm/static/theme.json": '{"fixture":true}\n',
                        "requirements.lock": "# Imported dependency fixture\n",
                        "pyproject.toml": "# Imported project fixture\n",
                        "LICENSE": "Imported license fixture\n",
                        "tests/test_brand_palette.py": "# Imported original test fixture\n",
                    }
                    excluded = {"crm/.local/excluded.py": "# Excluded fixture\n"}
                    stem = "crm"
                else:
                    selected = {
                        "internal/rag/fts5_index.py": "# Imported source fixture\ndef fts_tokenize(): return []\n",
                        "tests/test_fts5_index.py": "# Imported original test fixture\n",
                    }
                    excluded = {"unrelated.py": "# Excluded fixture\n"}
                    stem = "saber"
                excluded[".env"] = "IMPORTED_FIXTURE_ONLY=not-a-real-secret\n"
                for name, text in {**selected, **excluded}.items():
                    target = source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")

                def source_hashes():
                    return {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in source.rglob("*") if p.is_file()}

                before = source_hashes()
                output = base / "export" / "context"
                with contextlib.redirect_stdout(io.StringIO()):
                    load(kind).main(["--source", str(source), "--output", str(output)])
                self.assertEqual(before, source_hashes())
                manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(set(manifest["files"]), set(selected))
                self.assertIn(str(source.resolve()), manifest["source_origin"])
                receipt = json.loads((output.parent / f"{stem}-source-receipt.json").read_text())
                self.assertFalse(receipt["real_env_read"])
                self.assertFalse(receipt["production_data_included"])
                archive = output.parent / f"{stem}-context.tar.gz"
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), receipt["archive_sha256"])
                with tarfile.open(archive) as bundle:
                    names = set(bundle.getnames())
                    self.assertIn("manifest.json", names)
                    self.assertTrue({"source/" + name for name in selected} <= names)
                    self.assertTrue({"source/" + name for name in excluded}.isdisjoint(names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
