"""Explicit offline signature fixtures; these are not release evidence."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from supplychain import (
    image_reference,
    platform_reference,
    sign,
    verify_manifest,
    windows_private_acl,
)


class OfflineSigningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="drop-signature-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.root / "fixture.pub"
        self.public.write_bytes(
            self.private.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        self.fingerprint = hashlib.sha256(self.public.read_bytes()).hexdigest()
        self.sbom = self.root / "fixture.cdx.json"
        self.sbom.write_text(
            '{"bomFormat":"CycloneDX","fixture":"not an image inventory"}',
            encoding="utf-8",
        )
        self.manifest = self.root / "fixture.json"
        self.signature = self.root / "fixture.sig"
        self.data = {
            "release": "explicit-test-fixture",
            "public_key_sha256": self.fingerprint,
            "images": [
                {
                    "reference": name + ":20261018.1-rc2",
                    "image_id": "sha256:" + "a" * 64,
                    "sbom_file": self.sbom.name,
                    "sbom_sha256": hashlib.sha256(self.sbom.read_bytes()).hexdigest(),
                }
                for name in (
                    "drop-core",
                    "drop-agent",
                    "drop-api",
                    "drop-analyzer",
                    "drop-web",
                    "drop-ops",
                )
            ],
        }
        self.write()

    def write(self):
        self.manifest.write_bytes(json.dumps(self.data).encode())
        self.signature.write_bytes(
            base64.b64encode(self.private.sign(self.manifest.read_bytes()))
        )

    def verify(self):
        return verify_manifest(
            self.manifest, self.signature, self.public, self.fingerprint
        )

    def test_signed_manifest_and_bound_sbom(self):
        self.assertEqual(self.verify()["release"], "explicit-test-fixture")

    def test_modified_manifest_is_rejected(self):
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        with self.assertRaises(InvalidSignature):
            self.verify()

    def test_modified_sbom_is_rejected(self):
        self.sbom.write_bytes(self.sbom.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "SBOM"):
            self.verify()

    def test_unpinned_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "trust root"):
            verify_manifest(self.manifest, self.signature, self.public, "0" * 64)

    def test_signed_path_traversal_is_rejected(self):
        self.data["images"][0]["sbom_file"] = "../fixture.cdx.json"
        self.write()
        with self.assertRaises(ValueError):
            self.verify()

    def test_remote_image_reference_cannot_inject_shell(self):
        self.assertEqual(image_reference("drop-api:full-ready"), "drop-api:full-ready")
        for value in ["x;id", "x$(id)", "x\n", "--help", "x `id`"]:
            with self.assertRaises(ValueError):
                image_reference(value)

    def platform(self):
        sbom = self.root / "fixture-nginx.cdx.json"
        sbom.write_bytes(b'{"bomFormat":"CycloneDX","fixture":"platform test only"}')
        return {
            "reference": "drop-nginx:20261018.1-rc1",
            "image_id": "sha256:" + "b" * 64,
            "sbom_file": sbom.name,
            "sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
        }

    def sign_fixture(self, platform_records):
        private = self.root / "fixture-private"
        private.mkdir()
        (private / "release-signing.key").write_bytes(
            self.private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(
                    b"imported-temporary-test-password"
                ),
            )
        )
        (private / "release-signing.password").write_bytes(
            b"imported-temporary-test-password"
        )
        records = []
        for index, record in enumerate(self.data["images"]):
            path = self.root / f"app-{index}.record.json"
            path.write_text(json.dumps(record))
            records.append(path)
        platforms = []
        for index, record in enumerate(platform_records):
            path = self.root / f"platform-{index}.record.json"
            path.write_text(json.dumps(record))
            platforms.append(path)
        args = SimpleNamespace(
            private_directory=private,
            records=records,
            platform_records=platforms,
            output=self.manifest,
            release="imported-signing-fixture",
            source_commit=None,
            source_archive=None,
        )
        with patch("builtins.print"):
            sign(args)

    def test_real_signing_binds_optional_nginx_separately_from_six_apps(self):
        platform = self.platform()
        self.sign_fixture([platform])
        verified = self.verify()
        self.assertEqual(len(verified["images"]), 6)
        self.assertEqual(verified["platform_images"], [platform])
        self.assertEqual(
            len({record["reference"].split(":")[0] for record in verified["images"]}), 6
        )

    def test_real_six_application_signing_without_optional_platform(self):
        self.sign_fixture([])
        verified = self.verify()
        self.assertEqual(len(verified["images"]), 6)
        self.assertEqual(verified["platform_images"], [])

    def test_platform_release_syntax_and_calendar_are_not_one_tag_specific(self):
        for reference in (
            "drop-nginx:20261018.1-rc1",
            "drop-nginx:20280229.2-rc3",
            "drop-nginx:20261019.1",
        ):
            self.assertEqual(platform_reference(reference), reference)
        for reference in (
            "drop-nginx:20270229.1",
            "drop-nginx:20261018.0",
            "drop-nginx:20261018.1-rc0",
            "drop-nginx:20261018.1\n",
        ):
            with self.assertRaises(ValueError):
                platform_reference(reference)

    def test_unknown_platform_signing_is_rejected_before_loading_private_key(self):
        with patch("supplychain.serialization.load_pem_private_key") as load:
            with self.assertRaisesRegex(ValueError, "platform"):
                self.sign_fixture(
                    [self.platform() | {"reference": "drop-api:20261018.1-rc2"}]
                )
        load.assert_not_called()

    def test_platform_manifest_bytes_and_adjacent_sbom_tampering_are_rejected(self):
        self.data["platform_images"] = [self.platform()]
        self.write()
        self.verify()
        document = self.manifest.read_bytes()
        self.manifest.write_bytes(
            document.replace(b"drop-nginx:20261018.1-rc1", b"drop-nginx:20261018.1-rc9")
        )
        with self.assertRaises(InvalidSignature):
            self.verify()
        self.manifest.write_bytes(document)
        (self.root / "fixture-nginx.cdx.json").write_bytes(
            b"altered platform inventory"
        )
        with self.assertRaisesRegex(ValueError, "SBOM"):
            self.verify()

    def test_unknown_platform_duplicate_and_module_impersonation_are_rejected(self):
        platform = self.platform()
        for bad in (
            [platform | {"reference": "drop-redis:20261018.1-rc1"}],
            [platform | {"reference": "drop-nginx:latest"}],
            [platform | {"reference": "drop-nginx:20261018.1-rc0"}],
            [platform | {"reference": "drop-nginx:20260230.1-rc1"}],
            [platform | {"reference": "drop-api:20261018.1-rc2"}],
            [platform, platform],
            [platform | {"image_id": "mutable-image"}],
            [platform | {"sbom_sha256": "not-a-digest"}],
            [platform | {"sbom_file": "../fixture-nginx.cdx.json"}],
            [platform | {"sbom_file": "subdir\\fixture-nginx.cdx.json"}],
        ):
            with self.subTest(platform=bad):
                self.data["platform_images"] = bad
                self.write()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_signed_manifest_still_requires_exactly_six_application_modules(self):
        applications = list(self.data["images"])
        for bad in (
            applications[:5],
            applications + [self.platform()],
            applications[:5] + [applications[0]],
            applications[:5] + [self.platform()],
        ):
            with self.subTest(images=bad):
                self.data["images"] = bad
                self.data["platform_images"] = [self.platform()]
                self.write()
                with self.assertRaises(ValueError):
                    self.verify()


class WindowsPrivateACLTests(unittest.TestCase):
    @patch("supplychain.subprocess.run")
    def test_localized_account_output_is_not_decoded_or_used_as_a_grant(self, run):
        run.side_effect = [
            SimpleNamespace(
                stdout=b'"\xff\xfeaccount","S-1-5-21-100-200-300-1001"\r\n'
            ),
            SimpleNamespace(),
        ]
        windows_private_acl("fixture-private-directory")
        args = run.call_args_list[1].args[0]
        self.assertEqual(
            args[-2:], ["*S-1-5-21-100-200-300-1001:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"]
        )
        self.assertIn("/inheritance:r", args)

    @patch("supplychain.subprocess.run")
    def test_missing_or_ambiguous_sid_never_changes_permissions(self, run):
        for value in (b"no SID", b"S-1-5-18 S-1-5-21-1001"):
            run.reset_mock()
            run.return_value = SimpleNamespace(stdout=value)
            with self.assertRaises(RuntimeError):
                windows_private_acl("fixture-private-directory")
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
