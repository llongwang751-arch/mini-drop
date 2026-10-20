"""Collect exact local Docker-image SBOMs, serially, without exporting images."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

OUTPUT_ROOT = Path("/opt/drop-build/sbom-20261018")
SYFT_SHA256 = "dbf75864e7a7ff9e1fbf00552c31483f693188632a9f68f343cd7653dac513d6"
MAX_SBOM_BYTES = 32 * 1024 * 1024
MAX_DOCKER_LIST_BYTES = 65536
GIB = 1024**3
DIAGNOSTIC_TOOLS_BASE = "drop-core-full-tools-base:v2"
IMAGE = re.compile(
    r"drop-(core|agent|api|analyzer|web|ops|nginx):([0-9]{8}\.[1-9][0-9]*(?:-rc[1-9][0-9]*)?)"
)
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
INSPECT_FORMAT = '{"Id":{{json .Id}},"Os":{{json .Os}},"Architecture":{{json .Architecture}},"Size":{{json .Size}}}'


class Failure(Exception):
    """Only a fixed, nonsecret error code may cross the CLI boundary."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("SBOM_ARGUMENTS_REJECTED")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def image_spec(value: str) -> tuple[str, str]:
    parts = value.split("=")
    if (
        len(parts) != 2
        or not (IMAGE.fullmatch(parts[0]) or parts[0] == DIAGNOSTIC_TOOLS_BASE)
        or not IMAGE_ID.fullmatch(parts[1])
    ):
        raise Failure("SBOM_IMAGE_REJECTED")
    if parts[0] == DIAGNOSTIC_TOOLS_BASE:
        return parts[0], parts[1]
    try:
        tag_date = parts[0].split(":", 1)[1][:8]
        date(int(tag_date[:4]), int(tag_date[4:6]), int(tag_date[6:8]))
    except ValueError:
        raise Failure("SBOM_IMAGE_REJECTED") from None
    return parts[0], parts[1]


def unique_specs(values: list[str]) -> list[tuple[str, str]]:
    specs = [image_spec(value) for value in values]
    components = [
        "diagnostic-tools-base"
        if ref == DIAGNOSTIC_TOOLS_BASE
        else IMAGE.fullmatch(ref).group(1)
        for ref, _ in specs
    ]
    if not specs or len(specs) > 7 or len(set(components)) != len(components):
        raise Failure("SBOM_IMAGE_SET_REJECTED")
    return specs


def inspect_metadata(payload: bytes) -> dict:
    if len(payload) > 4096:
        raise Failure("SBOM_INSPECT_REJECTED")
    try:
        data = json.loads(payload)
        if (
            not isinstance(data, dict)
            or set(data) != {"Id", "Os", "Architecture", "Size"}
            or not isinstance(data["Id"], str)
            or not IMAGE_ID.fullmatch(data["Id"])
            or data["Os"] != "linux"
            or data["Architecture"] not in {"amd64", "arm64"}
            or type(data["Size"]) is not int
            or not 0 < data["Size"] <= 64 * GIB
        ):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise Failure("SBOM_INSPECT_REJECTED") from None
    return {
        "image_id": data["Id"],
        "os": data["Os"],
        "architecture": data["Architecture"],
        "size": data["Size"],
    }


def disk_metadata(payload: bytes, expected_id: str) -> dict:
    """Project just ID/byte size from one bounded, exact-reference image list.

    Docker 29.1.3 inspect.Size includes packed content only with containerd.
    The list endpoint's integer Size also includes unpacked snapshot usage.
    Image-list labels are never selected, retained in reports, or logged.
    """

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if not 0 < len(payload) <= MAX_DOCKER_LIST_BYTES:
            raise ValueError
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError
        item = data[0]
        if (
            not isinstance(item, dict)
            or not IMAGE_ID.fullmatch(expected_id)
            or item.get("Id") != expected_id
            or type(item.get("Size")) is not int
            or not 0 < item["Size"] <= 64 * GIB
        ):
            raise ValueError
        return {"image_id": expected_id, "disk_size_bytes": item["Size"]}
    except (ValueError, TypeError, RecursionError):
        raise Failure("SBOM_DISK_METADATA_REJECTED") from None


class DockerConnection(http.client.HTTPConnection):
    """Fixed local daemon socket; no TCP, proxy, context or environment input."""

    def __init__(self):
        super().__init__("localhost", timeout=30)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect("/var/run/docker.sock")


def pressure(payload: str) -> dict[str, float]:
    values = {}
    try:
        for line in payload.splitlines():
            fields = line.split()
            if fields and fields[0] in {"some", "full"}:
                if fields[0] in values:
                    raise ValueError
                pairs = dict(item.split("=", 1) for item in fields[1:])
                value = float(pairs["avg10"])
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError
                values[fields[0]] = value
        if set(values) != {"some", "full"}:
            raise ValueError
    except (ValueError, KeyError):
        raise Failure("SBOM_RESOURCE_METRICS_REJECTED") from None
    return values


def resource_gate(
    free_bytes: int,
    content_size: int,
    disk_size: int,
    meminfo: str,
    memory_psi: str,
    io_psi: str,
) -> None:
    try:
        if any(
            type(value) is not int or not 0 < value <= 64 * GIB
            for value in (content_size, disk_size)
        ):
            raise ValueError
        available = [
            line for line in meminfo.splitlines() if line.startswith("MemAvailable:")
        ]
        if len(available) != 1 or not re.fullmatch(
            r"MemAvailable:\s+[0-9]+ kB", available[0]
        ):
            raise ValueError
        memory = int(available[0].split()[1]) * 1024
    except ValueError:
        raise Failure("SBOM_RESOURCE_METRICS_REJECTED") from None
    mem, io = pressure(memory_psi), pressure(io_psi)
    if (
        # Do not multiply inspect.Size: on Docker 29.1.3 it excludes snapshots.
        # Budget packed tar + list content/snapshots + a production reserve.
        free_bytes < content_size + disk_size + GIB
        or memory < 768 * 1024**2
        or mem["some"] > 1
        or mem["full"] > 0.1
        or io["some"] > 5
        or io["full"] > 1
    ):
        raise Failure("SBOM_RESOURCE_GATE")


def secure_directory(path: Path, *, private: bool) -> None:
    data = path.lstat()
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISDIR(data.st_mode) or data.st_uid != 0 or data.st_mode & forbidden:
        raise Failure("SBOM_OUTPUT_DIRECTORY_REJECTED")


def prepare_root(path: Path) -> None:
    # No resolve(): it could hide a symlink in the trusted output ancestry.
    for ancestor in reversed(path.parents):
        secure_directory(ancestor, private=False)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    secure_directory(path, private=True)


def require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise Failure("SBOM_OUTPUT_EXISTS")


def same_file(path: Path, identity: os.stat_result) -> bool:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    return (current.st_dev, current.st_ino, current.st_mode) == (
        identity.st_dev,
        identity.st_ino,
        identity.st_mode,
    )


def write_exclusive(path: Path, payload: bytes) -> os.stat_result:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if same_file(path, identity):
            path.unlink()
        raise
    return identity


def validate_lock(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise Failure("SBOM_LOCK_REJECTED")


def same_lock(path: Path, identity: os.stat_result) -> bool:
    try:
        current = path.lstat()
        validate_lock(current)
    except (OSError, Failure):
        return False
    return (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)


@contextmanager
def collector_lock(root: Path):
    """Keep one persistent inode; the kernel releases flock on last FD close.

    Never unlink a lock file: a waiter may already hold the original inode.
    A legacy private lock marker is safe to reuse without reading its contents.
    No historical workspace is claimed or removed during lock recovery.
    """
    if sys.platform != "linux":
        raise Failure("SBOM_LOCK_UNAVAILABLE")
    import fcntl

    path = root / ".collector.lock"
    try:
        # NONBLOCK prevents a malicious FIFO from hanging before fstat rejects
        # it; NOFOLLOW rejects symlinks, including dangling ones, without writes.
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            0o600,
        )
    except OSError:
        raise Failure("SBOM_LOCK_REJECTED") from None
    locked = False
    try:
        identity = os.fstat(descriptor)
        validate_lock(identity)
        if not same_lock(path, identity):
            raise Failure("SBOM_LOCK_IDENTITY_CHANGED")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("SBOM_COLLECTOR_BUSY") from None
        except OSError:
            raise Failure("SBOM_LOCK_UNAVAILABLE") from None
        locked = True
        if not same_lock(path, identity):
            raise Failure("SBOM_LOCK_IDENTITY_CHANGED")
        yield descriptor
    finally:
        changed = locked and not same_lock(path, identity)
        # Close rather than LOCK_UN: an orphan Syft retaining its inherited FD
        # must keep the lock until it exits, even when only Python is killed.
        os.close(descriptor)
        if changed:
            raise Failure("SBOM_LOCK_IDENTITY_CHANGED")


@contextmanager
def owned_workspace(root: Path):
    path = Path(tempfile.mkdtemp(prefix=".drop-sbom-", dir=root))
    identity = path.lstat()
    try:
        yield path
    finally:
        # Only this invocation's freshly created inode can be removed. rmtree
        # unlinks contained symlinks rather than following their destinations.
        if (
            path.parent != root
            or not same_file(path, identity)
            or not stat.S_ISDIR(identity.st_mode)
        ):
            raise Failure("SBOM_TEMP_IDENTITY_CHANGED")
        shutil.rmtree(path)


def sanitized_sbom(payload: bytes, reference: str, image_id: str) -> tuple[bytes, int]:
    if not 0 < len(payload) <= MAX_SBOM_BYTES:
        raise Failure("SBOM_DOCUMENT_REJECTED")
    try:
        document = json.loads(payload)
        allowed = {
            "$schema",
            "bomFormat",
            "specVersion",
            "serialNumber",
            "version",
            "metadata",
            "components",
            "dependencies",
        }
        components = document.get("components")
        if (
            not isinstance(document, dict)
            or not set(document).issubset(allowed)
            or document.get("bomFormat") != "CycloneDX"
            or document.get("specVersion") not in {"1.5", "1.6", "1.7"}
            or not isinstance(components, list)
            or not 0 < len(components) <= 100000
            or not all(isinstance(component, dict) for component in components)
        ):
            raise ValueError
        # Syft 1.54.1 exports Docker Labels as metadata.properties. Omit image
        # metadata wholesale; retain package inventory/dependencies unchanged.
        document["metadata"] = {
            "tools": {
                "components": [
                    {"type": "application", "name": "syft", "version": "1.54.1"}
                ]
            },
            "component": {"type": "container", "name": reference, "version": image_id},
        }
        result = json.dumps(
            document, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if len(result) > MAX_SBOM_BYTES:
            raise ValueError
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise Failure("SBOM_DOCUMENT_REJECTED") from None
    return result, len(components)


def clean_environment(workspace: Path) -> dict[str, str]:
    # Never inherit shell credentials, proxy URLs, Docker contexts or Syft
    # overrides. An explicit Docker source cannot fall back to a registry.
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
        "TEMP": str(workspace),
        "TMP": str(workspace),
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "GOMAXPROCS": "1",
        "GOMEMLIMIT": "320MiB",
        "SYFT_PARALLELISM": "1",
        "SYFT_CHECK_FOR_APP_UPDATE": "false",
        "SYFT_GOLANG_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_JAVA_USE_NETWORK": "false",
        "SYFT_JAVASCRIPT_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_PYTHON_SEARCH_REMOTE_LICENSES": "false",
        "SYFT_CPP_VCPKG_ALLOW_GIT_CLONE": "false",
        "SYFT_ENRICH": "",
    }


class Host:
    def __init__(self, lock_descriptor: int | None = None):
        self.lock_descriptor = lock_descriptor

    def inspect(self, reference: str) -> dict:
        try:
            result = subprocess.run(
                [
                    "/usr/bin/docker",
                    "image",
                    "inspect",
                    "--format",
                    INSPECT_FORMAT,
                    reference,
                ],
                capture_output=True,
                timeout=30,
                check=False,
                env={
                    "PATH": "/usr/bin:/bin",
                    "DOCKER_HOST": "unix:///var/run/docker.sock",
                },
            )
            if result.returncode:
                raise Failure("SBOM_INSPECT_FAILED")
            return inspect_metadata(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("SBOM_INSPECT_FAILED") from None

    def disk_size(self, reference: str, expected_id: str) -> int:
        image_spec(reference + "=" + expected_id)
        connection = DockerConnection()
        try:
            query = urlencode(
                {"all": "1", "filters": json.dumps({"reference": [reference]})}
            )
            connection.request(
                "GET",
                "/v1.52/images/json?" + query,
                headers={"Accept": "application/json", "Connection": "close"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise Failure("SBOM_DISK_QUERY_FAILED")
            headers = response.getheaders()
            if len(headers) > 32 or sum(len(k) + len(v) for k, v in headers) > 8192:
                raise Failure("SBOM_DISK_QUERY_FAILED")
            lengths = [v for k, v in headers if k.lower() == "content-length"]
            encodings = [v for k, v in headers if k.lower() == "transfer-encoding"]
            if (
                len(lengths) > 1
                or len(encodings) > 1
                or (lengths and encodings)
                or (encodings and encodings != ["chunked"])
            ):
                raise Failure("SBOM_DISK_QUERY_FAILED")
            if lengths and (
                not re.fullmatch(r"[0-9]{1,6}", lengths[0])
                or not 0 < int(lengths[0]) <= MAX_DOCKER_LIST_BYTES
            ):
                raise Failure("SBOM_DISK_QUERY_FAILED")
            payload = response.read(MAX_DOCKER_LIST_BYTES + 1)
            if lengths and len(payload) != int(lengths[0]):
                raise Failure("SBOM_DISK_QUERY_FAILED")
            return disk_metadata(payload, expected_id)["disk_size_bytes"]
        except (OSError, http.client.HTTPException, ValueError):
            raise Failure("SBOM_DISK_QUERY_FAILED") from None
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def gate(self, output: Path, content_size: int, disk_size: int) -> None:
        resource_gate(
            shutil.disk_usage(output).free,
            content_size,
            disk_size,
            Path("/proc/meminfo").read_text(),
            Path("/proc/pressure/memory").read_text(),
            Path("/proc/pressure/io").read_text(),
        )

    def scan(self, syft: Path, image_id: str, output: Path, workspace: Path) -> None:
        try:
            result = subprocess.run(
                [
                    str(syft),
                    "docker:" + image_id,
                    "--quiet",
                    "--scope",
                    "squashed",
                    "--output",
                    "cyclonedx-json=" + str(output),
                ],
                cwd=workspace,
                env=clean_environment(workspace),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=()
                if self.lock_descriptor is None
                else (self.lock_descriptor,),
                timeout=1800,
                check=False,
            )
            if result.returncode:
                raise Failure("SBOM_SCAN_FAILED")
        except (OSError, subprocess.TimeoutExpired):
            raise Failure("SBOM_SCAN_FAILED") from None


def names(root: Path, reference: str) -> tuple[Path, Path]:
    stem = reference.replace(":", "-") + ".cdx"
    return root / (stem + ".json"), root / (stem + ".record.json")


def collect_one(
    host: Host, root: Path, syft: Path, reference: str, expected_id: str
) -> dict:
    image_spec(reference + "=" + expected_id)
    target, record_path = names(root, reference)
    require_absent(target)
    require_absent(record_path)
    metadata = host.inspect(reference)
    if metadata["image_id"] != expected_id:
        raise Failure("SBOM_IMAGE_ID_CHANGED")
    disk_size = host.disk_size(reference, expected_id)
    host.gate(root, metadata["size"], disk_size)
    with owned_workspace(root) as workspace:
        temporary = workspace / "inventory.cdx.json"
        host.scan(syft, expected_id, temporary, workspace)
        info = temporary.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_SBOM_BYTES
        ):
            raise Failure("SBOM_DOCUMENT_REJECTED")
        payload, count = sanitized_sbom(temporary.read_bytes(), reference, expected_id)
        if host.inspect(reference) != metadata:
            raise Failure("SBOM_IMAGE_ID_CHANGED")
        record = {
            "reference": reference,
            **metadata,
            "disk_size_bytes": disk_size,
            "source": "docker-daemon",
            "sbom_file": target.name,
            "sbom_sha256": sha256(payload),
            "components": count,
            "syft_binary_sha256": SYFT_SHA256,
        }
        if reference == DIAGNOSTIC_TOOLS_BASE:
            record["diagnostic_only"] = True
            record["release_component"] = False
        target_identity = write_exclusive(target, payload)
        try:
            write_exclusive(record_path, (json.dumps(record, indent=2) + "\n").encode())
        except BaseException:
            if same_file(target, target_identity):
                target.unlink()
            raise
        return record


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__)
    parser.add_argument(
        "--image", action="append", required=True, metavar="REF=sha256:ID"
    )
    parser.add_argument("--syft", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() != 0:
        raise Failure("SBOM_HOST_REJECTED")
    specs = unique_specs(args.image)
    if not args.syft.is_absolute():
        raise Failure("SBOM_SYFT_REJECTED")
    for ancestor in args.syft.parents:
        secure_directory(ancestor, private=False)
    syft_info = args.syft.lstat()
    if (
        not args.syft.is_absolute()
        or not stat.S_ISREG(syft_info.st_mode)
        or syft_info.st_nlink != 1
        or syft_info.st_uid != 0
        or syft_info.st_mode & 0o022
        or not syft_info.st_mode & 0o111
        or syft_info.st_size > 256 * 1024**2
        or sha256(args.syft.read_bytes()) != SYFT_SHA256
    ):
        raise Failure("SBOM_SYFT_REJECTED")
    os.umask(0o077)
    prepare_root(OUTPUT_ROOT)
    with collector_lock(OUTPUT_ROOT) as lock_descriptor:
        for reference, _ in specs:
            for target in names(OUTPUT_ROOT, reference):
                require_absent(target)
        host = Host(lock_descriptor=lock_descriptor)
        for reference, image_id in specs:
            print(
                json.dumps(
                    collect_one(host, OUTPUT_ROOT, args.syft, reference, image_id)
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        print(json.dumps({"error_code": str(error)}), file=sys.stderr)
        raise SystemExit(1) from None
    except (
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
        MemoryError,
        RecursionError,
    ):
        print('{"error_code":"SBOM_COLLECTION_FAILED"}', file=sys.stderr)
        raise SystemExit(1) from None
