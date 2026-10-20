"""spool 回归只使用本地小文件和假命令，不建立真实挂载。"""

import json
import stat
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "prepare-helper-spool.sh"


def load_script():
    source = SCRIPT.read_text(encoding="utf8").split("# PYTHON_BEGIN\n", 1)[1]
    source = source.rsplit("\n# PYTHON_END", 1)[0]
    module = types.ModuleType("helper_spool")
    exec(compile(source, str(SCRIPT), "exec"), module.__dict__)  # noqa: S102 - 仅执行仓库自有的内嵌脚本
    return module


class SpoolContractTests(unittest.TestCase):
    def test_fixed_root_and_physical_capacity_contract(self):
        module = load_script()
        self.assertEqual(module.SIZE, 2 * 1024**3)
        self.assertEqual(module.RESERVE, 1024**3)
        for root in ("/opt/drop-runtime", "/opt/drop-stage-full/runtime"):
            self.assertEqual(module.validate_root(root).as_posix(), root)
        for root in (
            "/",
            "/opt",
            "/tmp/x",
            "relative",
            "/opt/drop-runtime/../drop-runtime",
        ):
            with self.subTest(root=root), self.assertRaises(module.SpoolError):
                module.validate_root(root)

    def test_untrusted_directory_owner_symlink_and_write_bits_are_refused(self):
        module = load_script()
        for mode, owner in (
            (stat.S_IFLNK | 0o777, 0),
            (stat.S_IFDIR | 0o750, 10001),
            (stat.S_IFDIR | 0o770, 0),
        ):
            directory = Mock()
            directory.lstat.return_value = types.SimpleNamespace(
                st_mode=mode, st_uid=owner, st_gid=10001
            )
            with (
                self.subTest(mode=mode, owner=owner),
                self.assertRaises(module.SpoolError),
            ):
                module.secure_directory(directory)
        directory.lstat.return_value = types.SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=10001
        )
        module.secure_directory(directory, 0o750, 10001)

    def test_private_file_rejects_link_owner_mode_and_wrong_size(self):
        module = load_script()
        good = {
            "st_mode": stat.S_IFREG | 0o600,
            "st_uid": 0,
            "st_nlink": 1,
            "st_size": module.SIZE,
        }
        for change in (
            {"st_mode": stat.S_IFLNK | 0o600},
            {"st_uid": 10001},
            {"st_nlink": 2},
            {"st_mode": stat.S_IFREG | 0o640},
            {"st_size": 1},
        ):
            path = Mock()
            path.lstat.return_value = types.SimpleNamespace(**(good | change))
            with self.subTest(change=change), self.assertRaises(module.SpoolError):
                module.secure_file(path, module.SIZE)

    def test_service_orders_spool_before_docker_and_keeps_mount_on_stop(self):
        service = (SCRIPT.parent / "systemd/drop-helper-spool.service").read_text()
        self.assertIn("Before=docker.service", service)
        self.assertNotIn("RequiredBy=docker.service", service)
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("--root /opt/drop-runtime", service)
        self.assertNotIn("ExecStop=", service)


class FakeCommands:
    def __init__(self, module, root):
        self.module, self.root = module, root
        self.calls = []
        self.uuid = "532187fd-719c-49ad-b8d6-b73649d0059e"
        self.mounted = False
        self.fail = None
        self.back_file = str(root / module.IMAGE)
        self.fs_type = "ext4"
        self.block_count = 1
        self.mount_uuid = None
        self.mount_options = "rw,nodev,nosuid,noexec,relatime"
        self.source = "/dev/loop9"
        self.offset = 0
        self.readonly = False
        self.sizelimit = 0

    def __call__(self, argv, absent=False):
        self.calls.append(argv)
        name = argv[0]
        if name == self.fail:
            raise self.module.SpoolError("SPOOL_COMMAND_FAILED")
        if name == "findmnt":
            if not self.mounted:
                return None
            return json.dumps(
                {
                    "filesystems": [
                        {
                            "target": str(self.root / self.module.MOUNT),
                            "source": self.source,
                            "fstype": "ext4",
                            "uuid": self.mount_uuid or self.uuid,
                            "options": self.mount_options,
                            "fsroot": "/",
                        }
                    ]
                }
            )
        if name == "mkfs.ext4":
            self.uuid = argv[argv.index("-U") + 1]
            return ""
        if name == "blkid":
            return f"TYPE={self.fs_type}\nUUID={self.uuid}\n"
        if name == "dumpe2fs":
            return f"Block count: {self.block_count}\nBlock size: 4096\n"
        if name == "mount":
            self.mounted = True
            return ""
        if name == "losetup":
            return json.dumps(
                {
                    "loopdevices": [
                        {
                            "name": "/dev/loop9",
                            "back-file": self.back_file,
                            "offset": self.offset,
                            "sizelimit": self.sizelimit,
                            "ro": self.readonly,
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected fake command: {name}")


class SpoolFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.module = load_script()
        self.module.SIZE = 4096  # 不实际分配 2 GiB 测试文件，用受控边界输入验证预算。
        self.fake = FakeCommands(self.module, self.root)
        self.stack.enter_context(patch.object(self.module, "command", self.fake))
        self.stack.enter_context(
            patch.object(
                self.module.subprocess,
                "run",
                side_effect=AssertionError("No real commands allowed"),
            )
        )
        self.stack.enter_context(patch.object(self.module, "secure_directory"))
        self.stack.enter_context(
            patch.object(self.module, "secure_file", self.checked_file)
        )
        self.stack.enter_context(
            patch.object(self.module, "prepare_subdirectories", self.subdirectories)
        )
        self.stack.enter_context(patch.object(self.module, "sync_directory"))
        self.stack.enter_context(patch.object(self.module.os, "chown", create=True))
        self.stack.enter_context(
            patch.object(self.module.os, "O_NOFOLLOW", 0, create=True)
        )
        self.stack.enter_context(
            patch.object(self.module.os, "posix_fallocate", self.allocate, create=True)
        )
        self.stack.enter_context(
            patch.object(
                self.module.os,
                "statvfs",
                return_value=types.SimpleNamespace(f_bavail=4096 + 1024**3, f_frsize=1),
                create=True,
            )
        )

    def allocate(self, descriptor, offset, size):
        self.assertEqual(size, 4096)
        self.module.os.ftruncate(descriptor, size)

    def checked_file(self, path, size=None):
        info = path.lstat()
        if size is not None:
            self.module.require(info.st_size == size, "SPOOL_IMAGE_SIZE")
        return info

    def subdirectories(self, point):
        for name in ("cache", "incoming"):
            (point / name).mkdir(exist_ok=True)

    def prepare(self):
        return self.module.prepare_locked(self.root)

    def test_first_creation_and_idempotent_reuse_preserve_cached_data(self):
        self.assertEqual(self.prepare()["status"], "SPOOL_READY")
        image = self.root / self.module.IMAGE
        self.assertEqual(image.stat().st_size, 4096)
        profile = self.root / self.module.MOUNT / "cache" / "existing.profile"
        profile.write_bytes(b"imported toy")
        self.prepare()
        self.assertEqual(profile.read_bytes(), b"imported toy")
        self.assertEqual(sum(argv[0] == "mkfs.ext4" for argv in self.fake.calls), 1)
        self.assertEqual(sum(argv[0] == "mount" for argv in self.fake.calls), 1)

    def test_existing_incomplete_image_is_never_formatted(self):
        (self.root / self.module.IMAGE).write_bytes(b"existing unrelated evidence")
        with self.assertRaisesRegex(self.module.SpoolError, "INCOMPLETE"):
            self.prepare()
        self.assertFalse(any(argv[0] == "mkfs.ext4" for argv in self.fake.calls))

    def test_nonempty_unmounted_mountpoint_is_refused(self):
        point = self.root / self.module.MOUNT
        point.mkdir()
        (point / "unrelated").write_bytes(b"preserve")
        with self.assertRaisesRegex(self.module.SpoolError, "MOUNTPOINT_NOT_EMPTY"):
            self.prepare()
        self.assertEqual((point / "unrelated").read_bytes(), b"preserve")
        self.assertFalse((self.root / self.module.IMAGE).exists())

    def test_insufficient_host_reserve_refuses_before_creating_image(self):
        self.module.os.statvfs.return_value.f_bavail = 4096 + 1024**3 - 1
        with self.assertRaisesRegex(self.module.SpoolError, "HOST_SPACE"):
            self.prepare()
        self.assertFalse((self.root / self.module.IMAGE).exists())

    def test_format_failure_only_removes_own_unmounted_partial_image(self):
        self.fake.fail = "mkfs.ext4"
        with self.assertRaises(self.module.SpoolError):
            self.prepare()
        self.assertFalse((self.root / self.module.IMAGE).exists())
        self.assertFalse((self.root / self.module.META).exists())

    def test_mount_failure_preserves_completed_image_and_metadata(self):
        self.fake.fail = "mount"
        with self.assertRaises(self.module.SpoolError):
            self.prepare()
        self.assertTrue((self.root / self.module.IMAGE).exists())
        self.assertTrue((self.root / self.module.META).exists())
        self.fake.fail = None
        self.prepare()
        self.assertEqual(sum(argv[0] == "mkfs.ext4" for argv in self.fake.calls), 1)

    def test_wrong_filesystem_uuid_size_or_loop_backing_is_refused(self):
        for field, value in (
            ("fs_type", "xfs"),
            ("uuid", "133187fd-719c-49ad-b8d6-b73649d0059e"),
            ("block_count", 2),
            ("back_file", "/opt/unrelated.ext4"),
            ("mount_uuid", "133187fd-719c-49ad-b8d6-b73649d0059e"),
            ("source", "/dev/sda1"),
            ("mount_options", "rw,nodev,nosuid"),
            ("offset", 4096),
            ("readonly", True),
            ("sizelimit", 4096),
        ):
            with self.subTest(field=field):
                if not (self.root / self.module.META).exists():
                    self.prepare()
                original = getattr(self.fake, field)
                setattr(self.fake, field, value)
                with self.assertRaises(self.module.SpoolError):
                    self.prepare()
                setattr(self.fake, field, original)
        self.assertEqual(sum(argv[0] == "mkfs.ext4" for argv in self.fake.calls), 1)

    def test_replaced_or_malformed_metadata_does_not_reformat(self):
        self.prepare()
        metadata = self.root / self.module.META
        metadata.write_text('{"uuid":"incomplete"}')
        with self.assertRaises(self.module.SpoolError):
            self.prepare()
        self.assertEqual(sum(argv[0] == "mkfs.ext4" for argv in self.fake.calls), 1)

    def test_truncated_existing_image_is_preserved_and_never_formatted(self):
        self.prepare()
        image = self.root / self.module.IMAGE
        image.write_bytes(b"truncated imported fixture")
        with self.assertRaisesRegex(self.module.SpoolError, "IMAGE_SIZE"):
            self.prepare()
        self.assertEqual(image.read_bytes(), b"truncated imported fixture")
        self.assertEqual(sum(argv[0] == "mkfs.ext4" for argv in self.fake.calls), 1)

    def test_duplicate_metadata_fields_are_refused(self):
        self.prepare()
        metadata = self.root / self.module.META
        original = metadata.read_text()
        metadata.write_text('{"version":1,' + original[1:])
        with self.assertRaisesRegex(self.module.SpoolError, "METADATA_INVALID"):
            self.prepare()

    def test_cleanup_never_unlinks_a_replaced_file(self):
        path = Mock()
        path.lstat.return_value = types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=99
        )
        self.module.remove_owned_partial(path, (1, 98))
        path.unlink.assert_not_called()

    def test_second_preparer_fails_busy_before_any_format_or_mount(self):
        fake_fcntl = types.SimpleNamespace(
            LOCK_EX=2, LOCK_NB=4, flock=Mock(side_effect=BlockingIOError)
        )
        with (
            patch.dict(sys.modules, {"fcntl": fake_fcntl}),
            patch.object(self.module, "check_root"),
            self.assertRaisesRegex(self.module.SpoolError, "SPOOL_BUSY"),
        ):
            self.module.prepare(self.root)
        self.assertFalse(self.fake.calls)


if __name__ == "__main__":
    unittest.main()
