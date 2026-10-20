"""Analyzer 的运行配置、有效配置快照与资源上限。

配置在作业执行前确定；任务参数只能收紧预算，不能扩张运维给定的内存、CPU 或下载上限。
密钥通过独立读取路径提供，不出现在可展示配置或配置指纹中。
"""

import os
from dataclasses import dataclass
from pathlib import Path
import socket
import re

from .runtime import (
    ConfigurationError,
    endpoint,
    parser,
    require,
    resolve,
    snapshot,
    text,
)


@dataclass(frozen=True)
class Config:
    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str = "drop-artifacts"
    worker_id: str = "analyzer"
    lease_seconds: int = 60
    renew_seconds: int = 10
    poll_seconds: float = 2
    work_dir: str = "/tmp/drop-analyzer"
    health_file: str = "/tmp/drop-analyzer-health.json"
    s3_secure: bool = False
    queue: str = "all"
    analysis_memory_bytes: int = 512 * 1024 * 1024
    analysis_cpu_seconds: int = 90
    analysis_wall_seconds: int = 120
    analysis_download_bytes: int = 32 * 1024 * 1024
    analysis_temp_bytes: int = 6 * 32 * 1024 * 1024
    analysis_min_free_bytes: int = 16 * 1024 * 1024

    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9102
    otel_endpoint: str = ""

    @classmethod
    def load(cls, argv=None, *, require_credentials=True):
        args = parser().parse_args(argv)
        defaults = {
            "DATABASE_URL": "",
            "S3_ENDPOINT": "minio:9000",
            "S3_ACCESS_KEY": "",
            "S3_SECRET_KEY": "",
            "S3_BUCKET": "drop-artifacts",
            "WORKER_ID": socket.gethostname(),
            "LEASE_SECONDS": 60,
            "RENEW_SECONDS": 10,
            "POLL_SECONDS": 2.0,
            "WORK_DIR": "/tmp/drop-analyzer",
            "HEALTH_FILE": "/tmp/drop-analyzer-health.json",
            "S3_SECURE": False,
            "ANALYZER_QUEUE": "all",
            "ANALYSIS_MEMORY_BYTES": 512 * 1024**2,
            "ANALYSIS_CPU_SECONDS": 90,
            "ANALYSIS_WALL_SECONDS": 120,
            "ANALYSIS_DOWNLOAD_BYTES": 32 * 1024**2,
            "ANALYSIS_TEMP_BYTES": 192 * 1024**2,
            "ANALYSIS_MIN_FREE_BYTES": 16 * 1024**2,
            "ANALYZER_METRICS_HOST": "0.0.0.0",
            "ANALYZER_METRICS_PORT": 9102,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
        }
        try:
            # 只接受显式配置的 exporter 端点，拒绝环境中额外 SDK 覆盖项；
            # 否则实际传输或采样策略会偏离有效配置及其指纹。
            require(
                not any(
                    key.startswith("OTEL_")
                    and key
                    not in {
                        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT_FILE",
                    }
                    for key in os.environ
                ),
                "CONFIG_KEY_NOT_ALLOWED",
            )
            values = resolve(
                defaults, SECRETS, args, os.environ, "ANALYZER_CONFIG_FILE"
            )
            config = cls(
                **{
                    ATTRIBUTES.get(key, key.lower()): value
                    for key, value in values.items()
                }
            )
            config.validate(require_credentials=require_credentials)
            return config
        except ConfigurationError:
            raise
        except (ValueError, TypeError, OverflowError):
            raise ConfigurationError("CONFIG_VALUE_INVALID") from None

    @classmethod
    def from_env(cls):
        return cls.load([])

    def snapshot(self):
        values = {key: getattr(self, ATTRIBUTES.get(key, key.lower())) for key in KEYS}
        return snapshot(
            values, SECRETS, {"S3_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"}
        )

    def validate(self, *, require_credentials=True):
        require(
            not require_credentials
            or self.database_url
            and self.s3_access_key
            and self.s3_secret_key,
            "CONFIG_CREDENTIALS_REQUIRED",
        )
        require(
            15 <= self.lease_seconds <= 3600
            and 1 <= self.renew_seconds < self.lease_seconds / 2
        )
        require(0.1 <= self.poll_seconds <= 60)
        require(
            self.queue in {"all", "small", "high_memory"}
            and 128 * 1024**2 <= self.analysis_memory_bytes <= 8 * 1024**3
        )
        require(
            1 <= self.analysis_cpu_seconds <= 3600
            and 1 <= self.analysis_wall_seconds <= 3600
        )
        require(
            1 <= self.analysis_download_bytes <= 1024**3
            and 1024**2 <= self.analysis_temp_bytes <= 8 * 1024**3
            and 1024**2 <= self.analysis_min_free_bytes <= 8 * 1024**3
        )
        require(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.worker_id) is not None)
        require(
            re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.s3_bucket)
            is not None
        )
        for path in (self.work_dir, self.health_file):
            text(path, 512)
            require(path.startswith("/") or Path(path).is_absolute())
        require(
            0 <= self.metrics_port <= 65535
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", self.metrics_host) is not None
        )
        endpoint(self.s3_endpoint)
        require(
            "://" not in self.s3_endpoint and bool(self.s3_endpoint),
            "CONFIG_ENDPOINT_INVALID",
        )
        endpoint(self.otel_endpoint, allow_path=True, require_scheme=True)


SECRETS = frozenset({"DATABASE_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY"})
ATTRIBUTES = {
    "ANALYZER_QUEUE": "queue",
    "ANALYZER_METRICS_HOST": "metrics_host",
    "ANALYZER_METRICS_PORT": "metrics_port",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "otel_endpoint",
}
KEYS = (
    "DATABASE_URL",
    "S3_ENDPOINT",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_BUCKET",
    "WORKER_ID",
    "LEASE_SECONDS",
    "RENEW_SECONDS",
    "POLL_SECONDS",
    "WORK_DIR",
    "HEALTH_FILE",
    "S3_SECURE",
    "ANALYZER_QUEUE",
    "ANALYSIS_MEMORY_BYTES",
    "ANALYSIS_CPU_SECONDS",
    "ANALYSIS_WALL_SECONDS",
    "ANALYSIS_DOWNLOAD_BYTES",
    "ANALYSIS_TEMP_BYTES",
    "ANALYSIS_MIN_FREE_BYTES",
    "ANALYZER_METRICS_HOST",
    "ANALYZER_METRICS_PORT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)
