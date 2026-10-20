"""仅通过鉴权 S3 API 执行有界的 SSE-S3 迁移，校验失败即停止写入。

默认 dry-run；实际 apply 要求业务写入已停止且持有数据库维护栅栏。
对象名称、profile 字节及凭据都不进入日志。
"""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import hashlib
import json
import re
import sys
import time
from urllib.parse import urlsplit

from minio import Minio
from minio.commonconfig import CopySource, REPLACE
from minio.datatypes import parse_list_objects
from minio.error import S3Error
import psycopg
from psycopg.rows import dict_row
import urllib3


class MigrationFailure(Exception):
    """只有经过审查的调用点给出的固定错误码可以进入报告。"""


@dataclass(frozen=True)
class Evidence:
    size: int
    sha256: str

    def __post_init__(self):
        if (
            type(self.size) is not int
            or self.size < 0
            or not isinstance(self.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise MigrationFailure("INVALID_DATABASE_EVIDENCE")


@dataclass(frozen=True)
class Verified:
    size: int
    sha256: str
    etag: str
    content_type: str
    metadata: dict
    algorithm: str
    system_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    max_objects: int = 10000
    max_bytes: int = 2 * 1024**3
    max_object_bytes: int = 256 * 1024**2
    page_size: int = 100

    def __post_init__(self):
        for value, upper in (
            (self.max_objects, 1000000),
            (self.max_bytes, 1024**4),
            (self.max_object_bytes, 1024**3),
            (self.page_size, 1000),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise MigrationFailure("INVALID_LIMIT")


@dataclass
class ReadBudget:
    maximum: int
    used: int = 0

    def require(self, amount):
        if amount < 0 or self.used + amount > self.maximum:
            raise MigrationFailure("READ_BUDGET_EXCEEDED")

    def consume(self, amount):
        self.require(amount)
        self.used += amount


def header(headers, name, default=None):
    return next(
        (value for key, value in headers.items() if key.lower() == name.lower()),
        default,
    )


def algorithm(headers):
    value = header(headers, "x-amz-server-side-encryption")
    return value if value in {"AES256", "aws:kms"} else "absent"


def user_metadata(headers):
    return {
        key.lower()[11:]: value
        for key, value in headers.items()
        if key.lower().startswith("x-amz-meta-")
    }


def system_metadata(headers):
    # S3 REPLACE 也会替换 HTTP 表示与缓存字段，必须与同名 x-amz-meta-*
    # 用户元数据分开处理。minio-py 7.2.20 会将 Expires 静默转成用户元数据，
    # 因此在写入前拒绝这种不支持的表示字段；应用网关本身不接收此字段。
    if header(headers, "Expires") is not None:
        raise MigrationFailure("UNSUPPORTED_SYSTEM_METADATA")
    return {
        name: value
        for name in (
            "Content-Encoding",
            "Content-Language",
            "Content-Disposition",
            "Cache-Control",
        )
        if (value := header(headers, name)) is not None
    }


def etag(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise MigrationFailure("INVALID_OBJECT_ETAG")
    result = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    if not result or any(
        ord(char) < 33 or ord(char) > 126 or char == '"' for char in result
    ):
        raise MigrationFailure("INVALID_OBJECT_ETAG")
    return result


def close_response(response):
    try:
        response.close()
    finally:
        response.release_conn()


class S3Storage:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def require_unversioned(self):
        # 复制当前版本不能证明旧版本也已加密。
        if self.client.get_bucket_versioning(self.bucket).status is not None:
            raise MigrationFailure("VERSIONED_BUCKET_UNSUPPORTED")
        config = self.client.get_bucket_encryption(self.bucket)
        if config.rule.sse_algorithm != "AES256":
            raise MigrationFailure("BUCKET_AES256_DEFAULT_REQUIRED")

    def keys(self, page_size, max_pages):
        # 为固定 minio-py 7.2.20 使用显式有界适配器；公开 list_objects
        # 未提供有界页数或 max-keys 控制。
        token = None
        for _ in range(max_pages):
            query = {
                "list-type": "2",
                "max-keys": str(page_size),
                "encoding-type": "url",
            }
            if token is not None:
                query["continuation-token"] = token
            response = self.client._execute(
                "GET", self.bucket, query_params=query, preload_content=False
            )
            try:
                body = response.read(2 * 1024**2 + 1)
                if len(body) > 2 * 1024**2:
                    raise MigrationFailure("LIST_RESPONSE_LIMIT")
                parsed = urllib3.response.HTTPResponse(body=body, status=200)
                objects, truncated, next_token, _ = parse_list_objects(parsed)
            finally:
                close_response(response)
            if len(objects) > page_size:
                raise MigrationFailure("LIST_RESPONSE_LIMIT")
            for item in objects:
                # 未设置 delimiter 时，以斜杠结尾的 Contents 仍是真实零字节对象，
                # 应纳入迁移范围，不能当成虚拟目录跳过。
                if not item.object_name or item.is_delete_marker:
                    raise MigrationFailure("INVALID_LIST_ENTRY")
                yield item.object_name
            if not truncated:
                return
            if not objects or not next_token or next_token == token:
                raise MigrationFailure("LIST_CURSOR_DID_NOT_ADVANCE")
            if len(next_token) > 16384:
                raise MigrationFailure("LIST_CURSOR_LIMIT")
            token = next_token
        raise MigrationFailure("LIST_PAGE_LIMIT")

    def exists(self, key):
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                return False
            raise

    def verify(self, key, evidence, budget, max_object_bytes):
        stat = self.client.stat_object(self.bucket, key)
        if type(stat.size) is not int or not 0 <= stat.size <= max_object_bytes:
            raise MigrationFailure("OBJECT_LIMIT")
        budget.require(stat.size)
        expected_etag = etag(stat.etag)
        response = self.client.get_object(
            self.bucket, key, request_headers={"If-Match": '"' + expected_etag + '"'}
        )
        digest, total = hashlib.sha256(), 0
        try:
            headers = response.headers
            if response.status != 200 or etag(header(headers, "etag")) != expected_etag:
                raise MigrationFailure("SOURCE_CHANGED")
            if str(header(headers, "content-length")) != str(stat.size):
                raise MigrationFailure("OBJECT_SIZE_MISMATCH")
            for chunk in response.stream(64 * 1024, decode_content=False):
                if len(chunk) > 64 * 1024 or total + len(chunk) > stat.size:
                    raise MigrationFailure("OBJECT_SIZE_MISMATCH")
                budget.consume(len(chunk))
                total += len(chunk)
                digest.update(chunk)
            actual = Verified(
                total,
                digest.hexdigest(),
                expected_etag,
                header(headers, "content-type", "application/octet-stream"),
                user_metadata(headers),
                algorithm(headers),
                system_metadata(headers),
            )
            if (
                total != stat.size
                or actual.content_type != stat.content_type
                or actual.metadata != user_metadata(stat.metadata or {})
                or actual.algorithm != algorithm(stat.metadata or {})
                or actual.system_metadata != system_metadata(stat.metadata or {})
            ):
                raise MigrationFailure("OBJECT_METADATA_CHANGED")
        finally:
            close_response(response)
        if evidence and (actual.size, actual.sha256) != (
            evidence.size,
            evidence.sha256,
        ):
            raise MigrationFailure("AUTHORITATIVE_DIGEST_MISMATCH")
        return actual

    def copy(self, key, before):
        # MetadataDirective.REPLACE 允许同键复制；加密来自已核实的 bucket
        # 默认 AES256 策略，不使用客户端 SSE。
        metadata = {"Content-Type": before.content_type, **before.system_metadata}
        # 显式加前缀保留 content-type、x-amz-copy-source 等同名用户字段，
        # 避免 SDK 将它们重新解释为控制头。
        metadata.update(
            {"x-amz-meta-" + key: value for key, value in before.metadata.items()}
        )
        result = self.client.copy_object(
            self.bucket,
            key,
            CopySource(self.bucket, key, match_etag='"' + before.etag + '"'),
            metadata=metadata,
            metadata_directive=REPLACE,
        )
        return etag(result.etag)


class SQLRepository:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def quiescent(self, apply):
        """实际迁移期间持有维护 advisory lock 和表锁，防止对账器及残留写入者并发修改。"""
        if not apply:
            with nullcontext():
                yield
            return
        # 与现有 maintenance 对账器共用 advisory key；表锁进一步阻止
        # 意外仍在运行的写入者执行 DML。
        self.connection.execute("SET LOCAL lock_timeout = '3s'")
        row = self.connection.execute(
            "SELECT pg_try_advisory_xact_lock("
            "hashtextextended('drop-maintenance-v1',0)) AS yes"
        ).fetchone()
        if not row["yes"]:
            raise MigrationFailure("MAINTENANCE_ALREADY_RUNNING")
        self.connection.execute(
            "LOCK TABLE tasks,task_attempts,analysis_jobs,artifacts IN SHARE MODE"
        )
        self.assert_quiescent()
        yield
        self.assert_quiescent()

    def assert_quiescent(self):
        row = self.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM tasks WHERE collection_status "
            "NOT IN ('COLLECTED','FAILED','CANCELED')) OR EXISTS("
            "SELECT 1 FROM analysis_jobs WHERE status IN ('pending','running','retry')) AS yes"
        ).fetchone()
        if row["yes"]:
            raise MigrationFailure("ACTIVE_WRITERS")

    def expected(self, key):
        row = self.connection.execute(
            "SELECT size,sha256 FROM artifacts WHERE object_key=%s", (key,)
        ).fetchone()
        # 过期或隔离对象仍在迁移范围内，不能按状态过滤掉。
        return Evidence(row["size"], row["sha256"]) if row else None

    def keys(self, page_size, max_rows):
        with self.connection.cursor(name="encryption_artifact_keys") as cursor:
            cursor.execute("SELECT object_key FROM artifacts ORDER BY object_key")
            total = 0
            while rows := cursor.fetchmany(page_size):
                for row in rows:
                    total += 1
                    if total > max_rows:
                        raise MigrationFailure("DATABASE_SCAN_LIMIT")
                    yield row["object_key"]


def migrate_object(
    storage, key, evidence, *, apply, budget, max_object_bytes, assert_quiescent
):
    """先完整读取校验一个对象；apply 时同键复制，再复核内容、元数据与 AES256。

    读预算在修改前预留，修改前后检查维护栅栏；只看复制成功或响应头不能当作内容完整的证据。
    """
    before = storage.verify(key, evidence, budget, max_object_bytes)
    baseline_only = evidence is None
    if before.algorithm == "AES256":
        return "already_encrypted", baseline_only
    if not apply:
        return "would_encrypt", baseline_only
    assert_quiescent()
    # 任何修改前先预留完整读取预算，孤儿对象也必须计入。
    budget.require(before.size)
    copied_etag = etag(storage.copy(key, before))
    after = storage.verify(
        key, Evidence(before.size, before.sha256), budget, max_object_bytes
    )
    if after.etag != copied_etag:
        raise MigrationFailure("SOURCE_CHANGED")
    if (after.content_type, after.metadata, after.system_metadata) != (
        before.content_type,
        before.metadata,
        before.system_metadata,
    ):
        raise MigrationFailure("OBJECT_METADATA_CHANGED")
    if after.algorithm != "AES256":
        raise MigrationFailure("SSE_S3_NOT_VERIFIED")
    assert_quiescent()
    return "encrypted", baseline_only


def migrate(
    storage, repository, *, apply=False, confirm_writers_stopped=False, limits=None
):
    """在对象数、单对象大小和总读取预算内执行 dry-run 或迁移，返回不含敏感值的报告。

    apply 需要显式确认写入者已停止，并在数据库维护栅栏内运行；完整扫描、加密验证与数据完整性分开记录，
    任何中途失败都保留 incomplete 状态，不将部分成功报告成迁移完成。
    """
    limits = limits or Limits()
    budget = ReadBudget(limits.max_bytes)
    result = {
        "mode": "apply" if apply else "dry_run",
        "complete": False,
        "bucket_encryption_verified": False,
        "artifact_integrity_complete": False,
        "scan_complete": False,
        "checked": 0,
        "encrypted": 0,
        "already_encrypted": 0,
        "would_encrypt": 0,
        "baseline_only": 0,
        "database_missing": 0,
    }
    try:
        if apply and not confirm_writers_stopped:
            raise MigrationFailure("WRITERS_STOPPED_CONFIRMATION_REQUIRED")
        with repository.quiescent(apply):
            storage.require_unversioned()
            for key in storage.keys(
                limits.page_size, limits.max_objects // limits.page_size + 2
            ):
                if result["checked"] >= limits.max_objects:
                    raise MigrationFailure("OBJECT_COUNT_LIMIT")
                action, baseline = migrate_object(
                    storage,
                    key,
                    repository.expected(key),
                    apply=apply,
                    budget=budget,
                    max_object_bytes=limits.max_object_bytes,
                    assert_quiescent=repository.assert_quiescent,
                )
                result["checked"] += 1
                result[action] += 1
                result["baseline_only"] += int(baseline)
            for key in repository.keys(limits.page_size, limits.max_objects):
                if not storage.exists(key):
                    result["database_missing"] += 1
            if apply:
                repository.assert_quiescent()
            result["scan_complete"] = True
        result["bucket_encryption_verified"] = apply
        result["artifact_integrity_complete"] = result["database_missing"] == 0
        result["complete"] = apply and result["artifact_integrity_complete"]
    except MigrationFailure as error:
        result["complete"] = result["scan_complete"] = False
        result["error"] = str(error)
    except Exception:
        # SDK 或数据库异常可能携带签名请求细节与密钥，不直接写入报告。
        result["complete"] = result["scan_complete"] = False
        result["error"] = "STORAGE_OR_DATABASE_FAILURE"
    result["bytes_read"] = budget.used
    return result


def main(argv=None):
    # 延迟导入使已审查的迁移原语可由私有存储夹具以独立只读绑定方式复用。
    from ops_runtime import Config, ConfigurationError, VERSION, emit

    started = time.monotonic()
    try:
        config = Config.load(argv, mode="encryption")
        config.log_startup("drop-encryption-migration")
        limits = Limits(
            config["ENCRYPTION_MAX_OBJECTS"],
            config["ENCRYPTION_MAX_BYTES"],
            config["ENCRYPTION_MAX_OBJECT_BYTES"],
            config["ENCRYPTION_PAGE_SIZE"],
        )
        endpoint = urlsplit(config["S3_ENDPOINT"])
        client = Minio(
            endpoint.netloc,
            access_key=config["S3_ACCESS_KEY"],
            secret_key=config["S3_SECRET_KEY"],
            secure=endpoint.scheme == "https",
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=3, read=30),
                retries=urllib3.Retry(total=0),
            ),
        )
        with psycopg.connect(
            config["DATABASE_URL"], row_factory=dict_row, connect_timeout=5
        ) as connection:
            result = migrate(
                S3Storage(client, config["S3_BUCKET"]),
                SQLRepository(connection),
                apply=config.apply,
                confirm_writers_stopped=config.confirm_writers_stopped,
                limits=limits,
            )
    except ConfigurationError as error:
        result = {"complete": False, "scan_complete": False, "error": str(error)}
    except MigrationFailure as error:
        result = {"complete": False, "scan_complete": False, "error": str(error)}
    except Exception:
        result = {
            "complete": False,
            "scan_complete": False,
            "error": "STORAGE_OR_DATABASE_FAILURE",
        }
    emit(
        "drop-encryption-migration",
        VERSION,
        "encryption_migration_finished",
        stage="migration",
        level="error" if result.get("error") else "info",
        error_code=result.get("error", ""),
        duration_ms=int((time.monotonic() - started) * 1000),
        stream=sys.stderr,
    )
    # stdout 输出的是机器可读 CLI 报告，与日志流分开。
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result.get("scan_complete")
        and not result.get("database_missing")
        and not result.get("error")
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
