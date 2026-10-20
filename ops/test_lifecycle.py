"""Verify the actual lifecycle XML passed to the S3 SDK, using no live bucket."""
import unittest
import xml.etree.ElementTree as ET

from minio.xml import marshal
from maintenance import configure_lifecycle


class CaptureClient:
    def set_bucket_lifecycle(self, bucket, config):
        self.bucket = bucket
        self.xml = marshal(config)


class LifecycleTest(unittest.TestCase):
    def test_binary_and_manifest_do_not_expire_with_raw_inputs(self):
        client = CaptureClient()
        configure_lifecycle(client, "imported-lifecycle-fixture")
        root = ET.fromstring(client.xml)
        rules = {}
        for rule in root.findall("{*}Rule"):
            prefix = rule.findtext("{*}Filter/{*}Prefix")
            days = rule.findtext("{*}Expiration/{*}Days")
            if prefix and days:
                rules[prefix] = int(days)
        self.assertEqual(rules.get("raw/"), 14)
        self.assertEqual(rules.get("logs/"), 14)
        self.assertEqual(rules.get("manifest/"), 90)
        self.assertEqual(rules.get("binary/"), 90)


if __name__ == "__main__":
    unittest.main()
