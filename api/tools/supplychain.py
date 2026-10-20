"""收集精确镜像的 SBOM，对有界离线发布 manifest 签名并验签。

不会发布镜像、访问 Rekor/Fulcio 或宣称获得 CI/registry 信任；
私钥位于仓库外的受限目录。依赖 Python cryptography，sbom 子命令还需要 Syft。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_IMAGES = frozenset(
    {"drop-core", "drop-agent", "drop-api", "drop-analyzer", "drop-web", "drop-ops"}
)
PLATFORM_REFERENCE = re.compile(
    r"drop-nginx:([0-9]{8})\.[1-9][0-9]*(?:-rc[1-9][0-9]*)?\Z"
)
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SBOM_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,250}\.json\Z")


def platform_reference(value):
    matched = (
        PLATFORM_REFERENCE.fullmatch(value)
        if isinstance(value, str) and len(value) <= 160
        else None
    )
    if matched is None:
        raise ValueError("Unknown platform release image")
    stamp = matched[1]
    try:
        date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
    except ValueError:
        raise ValueError("Invalid platform release date") from None
    return value


def validate_records(images, platforms):
    if not isinstance(images, list) or len(images) != 6:
        raise ValueError("Final manifest requires exactly the six release images")
    if not isinstance(platforms, list) or len(platforms) > 1:
        raise ValueError("Final manifest permits at most one nginx platform image")
    names = []
    for image in images:
        reference = image.get("reference") if isinstance(image, dict) else None
        if not isinstance(reference, str) or ":" not in reference:
            raise ValueError("Invalid application image record")
        image_reference(reference)
        names.append(reference.split(":", 1)[0])
    if set(names) != APPLICATION_IMAGES:
        raise ValueError("Final manifest requires exactly the six release images")
    for image in platforms:
        platform_reference(image.get("reference") if isinstance(image, dict) else None)
    for image in images + platforms:
        if (
            not isinstance(image.get("image_id"), str)
            or not IMAGE_ID.fullmatch(image["image_id"])
            or not isinstance(image.get("sbom_sha256"), str)
            or not SHA256.fullmatch(image["sbom_sha256"])
            or not isinstance(image.get("sbom_file"), str)
            or not SBOM_BASENAME.fullmatch(image["sbom_file"])
        ):
            raise ValueError("Invalid exact image or adjacent SBOM record")


def read_records(paths, maximum):
    if not isinstance(paths, (list, tuple)) or len(paths) > maximum:
        raise ValueError("Too many release records")
    records = []
    for file in paths:
        path = Path(file)
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= 65536
        ):
            raise ValueError("Release record must be a bounded regular file")
        with path.open("rb") as stream:
            payload = stream.read(65537)
        if not 0 < len(payload) <= 65536:
            raise ValueError("Release record must be a bounded regular file")
        records.append(json.loads(payload))
    return records


def verify_sboms(directory, images):
    for image in images:
        artifact = directory / image["sbom_file"]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or digest(artifact) != image["sbom_sha256"]
        ):
            raise ValueError("Bound adjacent SBOM missing or digest differs")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def image_reference(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,250}", value):
        raise ValueError("Invalid image reference")
    return value


def command(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, check=True, **kwargs)


def ssh_prefix(host):
    if not re.fullmatch(r"(?:[a-z_][a-z0-9_-]*@)?[a-zA-Z0-9.-]+", host):
        raise ValueError("Invalid SSH host")
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", host]


def export_image(args):
    image = image_reference(args.image)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(
        command(
            ssh_prefix(args.host) + [shlex.join(["docker", "image", "inspect", image])]
        ).stdout
    )[0]
    # 报告主动排除镜像 Env、命令文本及 labels，避免携带凭据与内部配置。
    record = {
        "reference": image,
        "image_id": metadata["Id"],
        "architecture": metadata["Architecture"],
        "os": metadata["Os"],
        "size": metadata["Size"],
        "repo_digests": metadata.get("RepoDigests", []),
    }
    with tempfile.TemporaryFile() as error_log, target.open("wb") as file:
        result = subprocess.run(
            ssh_prefix(args.host)
            + [shlex.join(["docker", "image", "save", metadata["Id"]])],
            stdout=file,
            stderr=error_log,
            timeout=1800,
            check=False,
        )
    if result.returncode:
        target.unlink(missing_ok=True)
        raise RuntimeError("Image export failed; no artifact retained")
    record["archive_sha256"] = digest(target)
    record["archive_bytes"] = target.stat().st_size
    target.with_suffix(target.suffix + ".metadata.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record))


def sbom(args):
    archive = Path(args.archive).resolve()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Syft 临时文件留在制品磁盘，不写系统 TEMP 卷。
    temp = output.parent / ".syft-tmp"
    temp.mkdir(exist_ok=True)
    env = dict(
        os.environ,
        GOMAXPROCS="1",
        GOMEMLIMIT="384MiB",
        TEMP=str(temp),
        TMP=str(temp),
        SYFT_CHECK_FOR_APP_UPDATE="false",
    )
    command(
        [
            args.syft,
            "docker-archive:" + str(archive),
            "--quiet",
            "--scope",
            "squashed",
            "--output",
            "cyclonedx-json=" + str(output),
        ],
        env=env,
        timeout=1800,
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    if document.get("bomFormat") != "CycloneDX" or not document.get("components"):
        raise AssertionError("Syft did not generate a populated CycloneDX SBOM")
    metadata_path = archive.with_suffix(archive.suffix + ".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["archive_sha256"] == digest(archive), "Archive changed after export"
    record = metadata | {
        "sbom_file": output.name,
        "sbom_sha256": digest(output),
        "components": len(document["components"]),
        "spec_version": document["specVersion"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output.with_suffix(".record.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record))


def windows_private_acl(path):
    # Windows 账号名可能包含非 ASCII 字符；从原始输出提取 ASCII SID，
    # 避免依赖本地化控制台文本的解码。
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    identifiers = re.findall(rb"\bS-1-5(?:-\d+){1,15}\b", result.stdout)
    if len(identifiers) != 1:
        raise RuntimeError("Could not resolve the current Windows identity")
    identity = "*" + identifiers[0].decode("ascii")
    subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            identity + ":(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )


def private_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        windows_private_acl(path)
    else:
        path.chmod(0o700)
    return path


def keys(args):
    directory = private_directory(args.private_directory)
    private_file = directory / "release-signing.key"
    password_file = directory / "release-signing.password"
    public_file = Path(args.public_key).resolve()
    if private_file.exists() or password_file.exists() or public_file.exists():
        raise FileExistsError("Refusing to overwrite a signing key")
    password = secrets.token_urlsafe(48).encode()
    private = Ed25519PrivateKey.generate()
    private_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password),
        )
    )
    password_file.write_bytes(password)
    if os.name != "nt":
        private_file.chmod(0o600)
        password_file.chmod(0o600)
    public_file.parent.mkdir(parents=True, exist_ok=True)
    public_file.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    print(
        json.dumps(
            {
                "public_key_sha256": digest(public_file),
                "algorithm": "Ed25519",
                "private_directory": str(directory),
                "trust": "Project-local self-managed key only; pin this public key through a separate trusted channel.",
            }
        )
    )


def sign(args):
    records = read_records(args.records, 6)
    platforms = read_records(getattr(args, "platform_records", []), 1)
    validate_records(records, platforms)
    output = Path(args.output).resolve()
    verify_sboms(output.parent, records + platforms)
    directory = Path(args.private_directory).resolve()
    private = serialization.load_pem_private_key(
        (directory / "release-signing.key").read_bytes(),
        password=(directory / "release-signing.password").read_bytes(),
    )
    if not isinstance(private, Ed25519PrivateKey):
        raise TypeError("Expected Ed25519 signing key")
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    manifest = {
        "schema_version": 1,
        "release": args.release,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signature_algorithm": "Ed25519",
        "public_key_sha256": hashlib.sha256(public).hexdigest(),
        "images": records,
        "platform_images": platforms,
        "contracts": {
            file: digest(ROOT / file)
            for file in ["contracts/drop.proto", "contracts/task-kinds.json"]
        },
        "trust_boundary": "Offline project-local key signing exact image IDs and SBOM bytes. No registry publication/signature, CI provenance, Fulcio identity, Rekor transparency or native ARM hardware qualification is asserted.",
    }
    if args.source_commit:
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.source_commit):
            raise ValueError("Expected an exact source commit digest")
        manifest["source_commit"] = args.source_commit
    if args.source_archive:
        archive = Path(args.source_archive).resolve()
        manifest["source_archive"] = {
            "filename": archive.name,
            "sha256": digest(archive),
            "bytes": archive.stat().st_size,
        }
    data = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    output.write_bytes(data)
    output.with_suffix(".sig").write_bytes(base64.b64encode(private.sign(data)) + b"\n")
    print(
        json.dumps(
            {
                "manifest": str(output),
                "manifest_sha256": digest(output),
                "public_key_sha256": manifest["public_key_sha256"],
            }
        )
    )


def verify_manifest(manifest, signature, public_key, expected_fingerprint):
    if digest(public_key) != expected_fingerprint:
        raise ValueError("Public key differs from separately pinned trust root")
    public = serialization.load_pem_public_key(Path(public_key).read_bytes())
    data = Path(manifest).read_bytes()
    public.verify(base64.b64decode(Path(signature).read_bytes(), validate=False), data)
    document = json.loads(data)
    if document["public_key_sha256"] != expected_fingerprint:
        raise ValueError("Manifest key fingerprint differs")
    platforms = document.get("platform_images", [])
    validate_records(document["images"], platforms)
    verify_sboms(Path(manifest).parent, document["images"] + platforms)
    return document


def verify(args):
    document = verify_manifest(
        args.manifest, args.signature, args.public_key, args.expected_key_sha256
    )
    print(
        json.dumps(
            {
                "verified": True,
                "release": document["release"],
                "images": len(document["images"]),
                "platform_images": len(document.get("platform_images", [])),
                "public_key_sha256": args.expected_key_sha256,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("export")
    p.add_argument("--host", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(run=export_image)
    p = sub.add_parser("sbom")
    p.add_argument("--syft", required=True)
    p.add_argument("--archive", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(run=sbom)
    p = sub.add_parser("keys")
    p.add_argument("--private-directory", required=True)
    p.add_argument("--public-key", required=True)
    p.set_defaults(run=keys)
    p = sub.add_parser("sign")
    p.add_argument("--private-directory", required=True)
    p.add_argument("--release", required=True)
    p.add_argument("--records", nargs=6, required=True)
    p.add_argument("--platform-records", nargs=1, default=[])
    p.add_argument("--output", required=True)
    p.add_argument("--source-commit")
    p.add_argument("--source-archive")
    p.set_defaults(run=sign)
    p = sub.add_parser("verify")
    p.add_argument("--manifest", required=True)
    p.add_argument("--signature", required=True)
    p.add_argument("--public-key", required=True)
    p.add_argument("--expected-key-sha256", required=True)
    p.set_defaults(run=verify)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
