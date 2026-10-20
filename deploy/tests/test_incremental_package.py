"""验证增量交付白名单、敏感内容扫描及归档边界，输入均为本地合成夹具，不连接服务器。"""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('incremental', Path(__file__).resolve().parents[1] / 'package_incremental.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class DeliveryBoundaryTests(unittest.TestCase):
    def test_exact_known_values_are_detected_without_disclosure(self):
        secret = b'synthetic-' + b'credential-for-regression'
        result = package.scan({'fixture.txt': b'prefix ' + secret + b' suffix'}, (secret,))
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['known_credentials'], 1)
        self.assertNotIn(secret.decode(), str(result))
        self.assertNotIn('fixture.txt', str(result))

    def test_real_shaped_signatures_and_private_keys_are_detected(self):
        signed = b'https://example.invalid/object?' + b'X-Amz-Signature=' + b'a' * 64
        key = b'-----BEGIN ' + b'PRIVATE KEY-----\n' + b'A' * 80 + b'\n-----END ' + b'PRIVATE KEY-----'
        result = package.scan({'signature': signed, 'key': key}, ())
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['signed_url'], 1)
        self.assertEqual(result['counts']['pem_private_key'], 1)

    def test_nonsecret_templates_remain_distinct_from_actual_values(self):
        result = package.scan({'template': b'https://example.invalid/?X-Amz-Signature=<redacted>\n-----BEGIN (?:type) PRIVATE KEY-----'}, ())
        self.assertTrue(result['passed'])

    def test_api_key_shape_is_detected_without_a_real_key_fixture(self):
        result = package.scan({'fixture': b'sk' + b'-' + b'0' * 32}, ())
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['api_key_shape'], 1)

    def test_private_and_raw_inputs_are_excluded(self):
        for name in ['.local/admin_password', 'deploy/secrets/config.json', 'web/e2e/auth-state.json',
                     'web/node_modules/module/index.js', 'core/build/probe', 'core/tests/trace.data',
                     'core/fixtures/capture.pprof', 'deploy/.env.example', 'credentials.pem']:
            with self.subTest(path=name):
                self.assertFalse(package.allowed(package.ROOT / name))
        self.assertTrue(package.allowed(package.ROOT / 'core/src/runner.cpp'))
        self.assertTrue(package.allowed(package.ROOT / 'docs/evidence/malloc-20261020/production-final-regression.json'))


if __name__ == '__main__':
    unittest.main()
