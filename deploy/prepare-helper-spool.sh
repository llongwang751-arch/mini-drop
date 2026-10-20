#!/bin/sh
# Host-only provisioning. The helper receives directory binds, never SYS_ADMIN.
exec python3 - "$@" <<'DROP_HELPER_SPOOL_PY'
# PYTHON_BEGIN
import argparse
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

SIZE = 2 * 1024**3
RESERVE = 1024**3
ROOTS = frozenset({"/opt/drop-runtime", "/opt/drop-stage-full/runtime"})
IMAGE = "helper-spool.ext4"
META = "helper-spool.json"
MOUNT = "helper-spool"


class SpoolError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise SpoolError(code)


def validate_root(value):
    require(value in ROOTS, "SPOOL_ROOT_NOT_ALLOWED")
    return Path(value)


def secure_directory(path, mode=None, gid=None):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0, "SPOOL_UNSAFE_DIRECTORY")
    require(not info.st_mode & 0o022, "SPOOL_WRITABLE_ANCESTOR")
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode, "SPOOL_DIRECTORY_MODE")
    if gid is not None:
        require(info.st_gid == gid, "SPOOL_DIRECTORY_GROUP")


def check_root(root):
    for ancestor in reversed(root.parents):
        secure_directory(ancestor)
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o750)
        os.chown(root, 0, 10001)
        root.chmod(0o750)
    secure_directory(root, 0o750, 10001)
    require(root.resolve() == root, "SPOOL_ROOT_ESCAPE")


def secure_file(path, expected_size=None):
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600,
        "SPOOL_UNSAFE_FILE",
    )
    if expected_size is not None:
        require(info.st_size == expected_size, "SPOOL_IMAGE_SIZE")
    return info


def command(argv, absent=False):
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        timeout=90,
        check=False,
    )
    if absent and result.returncode == 1:
        return None
    require(
        result.returncode == 0 and len(result.stdout) <= 65536, "SPOOL_COMMAND_FAILED"
    )
    return result.stdout.decode("utf8", errors="strict")


def mount_info(point):
    output = command(
        [
            "findmnt",
            "--mountpoint",
            str(point),
            "--json",
            "--output",
            "TARGET,SOURCE,FSTYPE,UUID,OPTIONS,FSROOT",
        ],
        absent=True,
    )
    if output is None:
        return None
    rows = json.loads(output)["filesystems"]
    require(len(rows) == 1 and rows[0]["target"] == str(point), "SPOOL_MOUNT_TARGET")
    return rows[0]


def read_metadata(path, root):
    secure_file(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(2049)
    require(len(raw) <= 2048, "SPOOL_METADATA_INVALID")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "SPOOL_METADATA_INVALID")
            result[key] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique_pairs)
    expected = {"version", "root", "image", "bytes", "filesystem", "uuid"}
    require(isinstance(data, dict) and set(data) == expected, "SPOOL_METADATA_INVALID")
    require(
        type(data["version"]) is int
        and data["version"] == 1
        and data["root"] == str(root)
        and data["image"] == IMAGE
        and type(data["bytes"]) is int
        and data["bytes"] == SIZE
        and data["filesystem"] == "ext4"
        and str(uuid.UUID(data["uuid"])) == data["uuid"],
        "SPOOL_METADATA_INVALID",
    )
    return data


def verify_image(image, data):
    secure_file(image, SIZE)
    fields = dict(
        line.split("=", 1)
        for line in command(["blkid", "-p", "-o", "export", str(image)]).splitlines()
        if "=" in line
    )
    require(
        fields.get("TYPE") == "ext4" and fields.get("UUID") == data["uuid"],
        "SPOOL_FILESYSTEM_IDENTITY",
    )
    fields = dict(
        line.split(":", 1)
        for line in command(["dumpe2fs", "-h", str(image)]).splitlines()
        if ":" in line
    )
    require(
        int(fields["Block count"].strip()) * int(fields["Block size"].strip()) == SIZE,
        "SPOOL_FILESYSTEM_SIZE",
    )


def verify_mount(point, image, data, mounted):
    require(
        mounted["target"] == str(point)
        and mounted["fstype"] == "ext4"
        and mounted["uuid"] == data["uuid"]
        and mounted["fsroot"] == "/"
        and {"rw", "nodev", "nosuid", "noexec"} <= set(mounted["options"].split(",")),
        "SPOOL_MOUNT_IDENTITY",
    )
    source = mounted["source"]
    require(re.fullmatch(r"/dev/loop[0-9]+", source) is not None, "SPOOL_LOOP_SOURCE")
    rows = json.loads(
        command(
            [
                "losetup",
                "--json",
                "--list",
                "--output",
                "NAME,BACK-FILE,OFFSET,SIZELIMIT,RO",
                source,
            ]
        )
    )["loopdevices"]
    require(len(rows) == 1, "SPOOL_LOOP_IDENTITY")
    row = rows[0]
    require(
        row["name"] == source
        and row["back-file"] == str(image)
        and int(row["offset"]) == 0
        and int(row["sizelimit"]) == 0
        and row["ro"] in (False, 0, "0"),
        "SPOOL_LOOP_IDENTITY",
    )


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def spool_lock(root):
    import fcntl

    path = root / ".helper-spool.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        secure_file(path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SpoolError("SPOOL_BUSY") from None
        yield
    finally:
        os.close(descriptor)


def remove_owned_partial(path, identity):
    if identity is None:
        return
    info = path.lstat()
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        path.unlink()


def create_image(root, image, metadata):
    space = os.statvfs(root)
    require(space.f_bavail * space.f_frsize >= SIZE + RESERVE, "SPOOL_HOST_SPACE")
    descriptor = os.open(
        image, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    created = os.fstat(descriptor)
    image_identity = created.st_dev, created.st_ino
    metadata_identity = None
    try:
        try:
            try:
                os.posix_fallocate(descriptor, 0, SIZE)
            except (AttributeError, OSError) as error:
                require(
                    isinstance(error, AttributeError)
                    or error.errno in {errno.ENOSYS, errno.EOPNOTSUPP},
                    "SPOOL_ALLOCATION_FAILED",
                )
                os.ftruncate(descriptor, SIZE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        info = secure_file(image, SIZE)
        require((info.st_dev, info.st_ino) == image_identity, "SPOOL_IMAGE_REPLACED")
        data = {
            "version": 1,
            "root": str(root),
            "image": IMAGE,
            "bytes": SIZE,
            "filesystem": "ext4",
            "uuid": str(uuid.uuid4()),
        }
        command(
            [
                "mkfs.ext4",
                "-F",
                "-m",
                "0",
                "-U",
                data["uuid"],
                "-L",
                "drop-helper-spool",
                str(image),
            ]
        )
        verify_image(image, data)
        descriptor = os.open(
            metadata, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        info = os.fstat(descriptor)
        metadata_identity = info.st_dev, info.st_ino
        with os.fdopen(descriptor, "w", encoding="utf8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(root)
        return data
    except Exception:
        # Only unlink this invocation's incomplete files after positively
        # observing no mount. A completed image survives later mount failures.
        try:
            if mount_info(root / MOUNT) is None:
                remove_owned_partial(metadata, metadata_identity)
                remove_owned_partial(image, image_identity)
        except Exception:
            pass
        raise


def prepare_subdirectories(point):
    for name, mode, gid in (("cache", 0o700, 0), ("incoming", 0o1770, 10001)):
        path = point / name
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            os.chown(path, 0, gid)
            path.chmod(mode)
        # The sticky incoming directory is intentionally group-writable.
        if name == "incoming":
            info = path.lstat()
            require(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == 0
                and info.st_gid == gid
                and stat.S_IMODE(info.st_mode) == mode,
                "SPOOL_INCOMING_IDENTITY",
            )
        else:
            secure_directory(path, mode, gid)


def prepare_locked(root):
    image, metadata, point = root / IMAGE, root / META, root / MOUNT
    if not point.exists() and not point.is_symlink():
        point.mkdir(mode=0o750)
        os.chown(point, 0, 10001)
        point.chmod(0o750)
    secure_directory(point)
    mounted = mount_info(point)
    if mounted is None:
        require(next(point.iterdir(), None) is None, "SPOOL_MOUNTPOINT_NOT_EMPTY")
    existing = (
        image.exists()
        or image.is_symlink()
        or metadata.exists()
        or metadata.is_symlink()
    )
    if existing:
        require(image.exists() and metadata.exists(), "SPOOL_INCOMPLETE_EXISTING_IMAGE")
        data = read_metadata(metadata, root)
        verify_image(image, data)
    else:
        require(mounted is None, "SPOOL_UNEXPECTED_MOUNT")
        data = create_image(root, image, metadata)
    if mounted is None:
        command(
            [
                "mount",
                "-t",
                "ext4",
                "-o",
                "loop,rw,nodev,nosuid,noexec",
                str(image),
                str(point),
            ]
        )
        mounted = mount_info(point)
        require(mounted is not None, "SPOOL_MOUNT_MISSING")
    verify_mount(point, image, data, mounted)
    # mkfs creates a root:root filesystem root. Grant traversal to the shared
    # target group, keeping both private image metadata and cache inaccessible.
    secure_directory(point)
    os.chown(point, 0, 10001)
    point.chmod(0o750)
    prepare_subdirectories(point)
    return {"status": "SPOOL_READY", "bytes": SIZE, "root": str(root)}


def prepare(root):
    check_root(root)
    with spool_lock(root):
        return prepare_locked(root)


def main():
    parser = argparse.ArgumentParser(
        description="Prepare the fixed-size host helper spool"
    )
    parser.add_argument("--root", default="/opt/drop-runtime")
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, "SPOOL_ROOT_REQUIRED")
        print(json.dumps(prepare(validate_root(args.root)), sort_keys=True))
    except SpoolError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("SPOOL_PREPARATION_FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# PYTHON_END
DROP_HELPER_SPOOL_PY
