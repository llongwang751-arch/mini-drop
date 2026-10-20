"""Build input rejection checks; no compiler, network or real /opt write required."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class OfflineProfilerInputTests(unittest.TestCase):
    def rejected(self, size, expected):
        source = (Path(__file__).resolve().parents[1] / 'tools-install.py').read_text()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'input.tar.gz'
            with archive.open('wb') as output:
                output.truncate(size)
            # Redirect only the fixed build output directory for the isolated test.
            self.assertEqual(source.count("pathlib.Path('/opt/drop-tools')"), 1)
            source = source.replace("pathlib.Path('/opt/drop-tools')", f'pathlib.Path({str(root / "tools")!r})')
            script = root / 'installer.py'
            script.write_text(source, encoding='utf-8')
            harness = "import runpy,sys,urllib.request,platform; platform.machine=lambda:'x86_64'; urllib.request.urlopen=lambda *a,**k:sys.exit('UNEXPECTED_NETWORK'); runpy.run_path(sys.argv[1],run_name='__main__')"
            process = subprocess.run([sys.executable, '-c', harness, str(script)],
                                     env=dict(os.environ, DROP_ASYNC_PROFILER_ARCHIVE=str(archive)),
                                     capture_output=True, text=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(expected, process.stderr)
            self.assertNotEqual(process.stderr.strip().splitlines()[-1], 'UNEXPECTED_NETWORK')
            self.assertFalse((root / 'tools/manifest.json').exists())
            self.assertFalse((root / 'tools/venv').exists())

    def test_corrupt_offline_release_rejected_before_extract_or_install(self):
        self.rejected(32, 'Official async-profiler release SHA256 mismatch')

    def test_oversized_offline_release_rejected_before_hash_or_extract(self):
        self.rejected(32 * 1024 * 1024 + 1, 'async-profiler release exceeds build input limit')


if __name__ == '__main__':
    unittest.main()
