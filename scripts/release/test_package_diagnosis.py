"""Packaging security boundaries; no model requests, build, Docker or server."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("package_diagnosis",Path(__file__).with_name("package_diagnosis.py"))
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)

class PackageBoundaryTests(unittest.TestCase):
    def test_workspace_selection_excludes_deployment_secrets_and_ignored_paths(self):
        files=package.source_files(package.ROOT)
        names=[p.relative_to(package.ROOT).as_posix() for p in files]
        self.assertIn("api/internal/service/diagnosis.go",names)
        self.assertIn("db/migrations/006_diagnosis_agent.sql",names)
        self.assertIn("web/src/components/DiagnosisAgent.tsx",names)
        self.assertIn("integrations/crm-python/harness/test_source_contract.py",names)
        self.assertIn("ops/code_executor.py",names)
        self.assertIn("db/migrations/008_code_repairs.sql",names)
        for name in names:
            self.assertFalse(any(part in package.BLOCKED for part in Path(name).parts))
            self.assertNotIn("ai_api_key",name)
            self.assertNotIn(".pem",name)
            self.assertFalse(Path(name).name.startswith(".env"))
    def test_credential_content_is_rejected_even_in_allowed_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"config.json"
            for raw in [b'{"key":"'+b"sk-"+b"a"*32+b'"}',b"-----BEGIN PRIVATE KEY-----\nfixture",b"-----BEGIN RSA PRIVATE KEY-----\nfixture"]:
                p.write_bytes(raw)
                with self.assertRaisesRegex(ValueError,"CREDENTIAL_CONTENT_REJECTED"):
                    package.safe_bytes(p)
            p.write_bytes(b'{"provider":"deepseek","configured":false}')
            self.assertEqual(package.safe_bytes(p),p.read_bytes())

if __name__=="__main__":unittest.main()
