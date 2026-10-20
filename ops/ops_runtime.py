"""Bounded native configuration and structured logs; no network dependencies."""

import argparse
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import unicodedata
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Only fixed public error codes are exposed, never rejected values."""


def require(condition, code="CONFIG_VALUE_INVALID"):
    if not condition:
        raise ConfigurationError(code)


def text(value, maximum=1024):
    require(isinstance(value, str) and len(value) <= maximum)
    require(not any(unicodedata.category(char) in {"Cc", "Cf"} for char in value))
    return value


def read_text(path, maximum=65536):
    text(path, 4096)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(info.st_mode) and info.st_size <= maximum,
                "CONFIG_FILE_INVALID",
            )
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum, "CONFIG_FILE_INVALID")
        return raw.decode("utf8")
    except (OSError, UnicodeError):
        raise ConfigurationError("CONFIG_FILE_INVALID") from None


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ConfigurationError("CONFIG_ARGUMENT_INVALID")


def parser():
    result = SafeParser(description="Load bounded native JSON configuration")
    result.add_argument("--config", help="JSON configuration file")
    result.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override an allowlisted non-secret setting",
    )
    return result


def resolve(defaults, secrets, args, environ, config_env):
    """CLI > environment (_FILE first) > JSON (_FILE first) > defaults."""
    source = {}
    path = args.config or environ.get(config_env)
    if path:

        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, "CONFIG_DUPLICATE_KEY")
                result[key] = value
            return result

        try:
            source = json.loads(
                read_text(path),
                object_pairs_hook=pairs,
                parse_constant=lambda _: require(False),
            )
        except (ValueError, RecursionError):
            raise ConfigurationError("CONFIG_FILE_INVALID") from None
        require(isinstance(source, dict), "CONFIG_FILE_INVALID")
        allowed = (set(defaults) - secrets) | {key + "_FILE" for key in defaults}
        require(set(source) <= allowed, "CONFIG_KEY_NOT_ALLOWED")
        for value in source.values():
            require(
                isinstance(value, (str, int, float, bool))
                and not isinstance(value, (dict, list))
            )
            if isinstance(value, str):
                text(value)
    overrides = {}
    require(len(args.set) <= len(defaults), "CONFIG_ARGUMENT_INVALID")
    for assignment in args.set:
        text(assignment, 2048)
        key, separator, value = assignment.partition("=")
        require(
            separator
            and key in defaults
            and key not in secrets
            and key not in overrides,
            "CONFIG_KEY_NOT_ALLOWED",
        )
        overrides[key] = value
    values = {}
    for key, default in defaults.items():
        if key in overrides:
            value = overrides[key]
        elif key + "_FILE" in environ:
            value = read_text(environ[key + "_FILE"], 8192).rstrip("\r\n")
        elif key in environ:
            value = environ[key]
        elif key + "_FILE" in source:
            value = read_text(source[key + "_FILE"], 8192).rstrip("\r\n")
        else:
            value = source.get(key, default)
        if isinstance(value, str):
            text(value, 8192 if key in secrets else 1024)
        if type(default) is bool:
            require(
                type(value) is bool
                or isinstance(value, str)
                and value.lower() in {"true", "false"}
            )
            value = value if type(value) is bool else value.lower() == "true"
        elif type(default) is int:
            require(
                type(value) is int
                or isinstance(value, str)
                and re.fullmatch(r"[0-9]{1,20}", value) is not None
            )
            value = int(value)
        elif type(default) is float:
            require(type(value) in {int, float} or isinstance(value, str))
            try:
                value = float(value)
            except ValueError:
                raise ConfigurationError("CONFIG_VALUE_INVALID") from None
            require(math.isfinite(value))
        else:
            require(isinstance(value, str))
        values[key] = value
    return values


def endpoint(value, *, allow_path=False, require_scheme=False):
    if not value:
        return value
    parsed = urlsplit(value if "://" in value else "http://" + value)
    try:
        require(parsed.scheme in {"http", "https"} and bool(parsed.hostname))
        require(
            parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
        require(allow_path or parsed.path in {"", "/"})
        require(parsed.port is None or 1 <= parsed.port <= 65535)
        require(not require_scheme or "://" in value)
    except ValueError:
        raise ConfigurationError("CONFIG_ENDPOINT_INVALID") from None
    return value


def snapshot(values, secrets, endpoints=()):
    # Secret contents AND secret-source paths are absent from hash input.
    canonical = {
        key: "[redacted]" if key in secrets else value for key, value in values.items()
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    visible = {
        key: "[endpoint]"
        if value and (key in endpoints or isinstance(value, str) and "://" in value)
        else value
        for key, value in canonical.items()
    }
    return visible, digest


def emit(
    service,
    version,
    message,
    *,
    level="info",
    stage="",
    request_id="",
    task_id="",
    attempt_id="",
    agent_id="",
    duration_ms=0,
    error_code="",
    stream=None,
    **fields,
):
    def identifier(value):
        value = str(value)
        return value if re.fullmatch(r"[A-Za-z0-9_.:-]{0,128}", value) else ""

    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level if level in {"info", "warning", "error"} else "error",
        "service": service,
        "version": version,
        "request_id": identifier(request_id),
        "task_id": identifier(task_id),
        "attempt_id": identifier(attempt_id),
        "agent_id": identifier(agent_id),
        "stage": identifier(stage),
        "duration_ms": duration_ms
        if type(duration_ms) is int and 0 <= duration_ms < 2**53
        else 0,
        "error_code": identifier(error_code),
        "message": identifier(message) or "invalid_log_event",
    }
    for key in (
        "analysis_job_id",
        "analysis_attempt",
        "worker_id",
        "lease_seconds",
        "concurrency",
        "total_samples",
        "node_count",
        "retryable",
        "error_type",
        "expired",
        "missing",
        "quarantined",
        "checked",
        "orphans",
        "multipart_aborted",
        "errors",
        "mode",
        "config_hash",
        "component",
    ):
        value = fields.get(key)
        if (
            type(value) is bool
            or type(value) is int
            and abs(value) < 2**63
            or isinstance(value, str)
            and identifier(value)
        ):
            if key != "config_hash" or re.fullmatch(r"[a-f0-9]{64}", str(value)):
                record[key] = value
    if "effective_config" in fields:
        visible = {}
        supplied = fields["effective_config"]
        if isinstance(supplied, dict) and len(supplied) <= 64:
            for key, value in supplied.items():
                if (
                    not isinstance(key, str)
                    or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is None
                ):
                    continue
                if any(
                    part in key
                    for part in (
                        "PASSWORD",
                        "SECRET",
                        "TOKEN",
                        "ACCESS_KEY",
                        "DATABASE_URL",
                    )
                ):
                    value = "[redacted]"
                elif "ENDPOINT" in key or isinstance(value, str) and "://" in value:
                    value = "[endpoint]" if value else ""
                elif isinstance(value, str):
                    try:
                        text(value, 512)
                    except ConfigurationError:
                        continue
                elif type(value) not in {int, float, bool}:
                    continue
                visible[key] = value
        record["effective_config"] = visible
    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > 16384:
        record.pop("effective_config", None)
        record["error_code"] = "LOG_FIELDS_LIMIT"
        encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    print(encoded, file=stream or sys.stdout, flush=True)


def install_logging(service, version):
    class SafeHandler(logging.Handler):
        def emit(self, record):
            # Library messages/format arguments may contain credentials or URLs.
            # Preserve bounded module/class identifiers instead of their text.
            emit(
                service,
                version,
                "library_log",
                stage="dependency",
                level="error" if record.levelno >= logging.ERROR else "warning",
                component=record.name,
                error_type=record.exc_info[0].__name__ if record.exc_info else "",
                stream=sys.stderr,
            )

    root = logging.getLogger()
    root.handlers = [SafeHandler()]
    root.setLevel(logging.WARNING)


# Kept self-contained in the Ops image; no runtime import from Analyzer.
VERSION = "0.2.3"
SECRETS = frozenset(
    {"DATABASE_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "ALERT_WEBHOOK_TOKEN"}
)


class Config:
    def __init__(self, values, mode, apply=False, confirm_writers_stopped=False):
        from types import MappingProxyType

        self.values = MappingProxyType(values)
        self.mode = mode
        self.apply = apply
        self.confirm_writers_stopped = confirm_writers_stopped

    def __getitem__(self, key):
        return self.values[key]

    @classmethod
    def load(cls, argv=None, *, mode="maintenance", require_credentials=True):
        require(mode in {"maintenance", "encryption"})
        arguments = parser()
        defaults = {
            "DATABASE_URL": "",
            "S3_ACCESS_KEY": "",
            "S3_SECRET_KEY": "",
            "S3_ENDPOINT": "http://storage-backend:9000"
            if mode == "encryption"
            else "http://minio:9000",
            "S3_BUCKET": "drop-artifacts",
        }
        aliases = {}
        if mode == "maintenance":
            defaults.update(
                {
                    "ALERT_WEBHOOK_TOKEN": "",
                    "MAINTENANCE_INTERVAL_SECONDS": 300,
                    "MAINTENANCE_METRICS_HOST": "0.0.0.0",
                    "MAINTENANCE_METRICS_PORT": 9105,
                    "MONITORING_STATUS_FILE": "/var/drop-monitoring/status.prom",
                }
            )
        else:
            defaults.update(
                {
                    "ENCRYPTION_MAX_OBJECTS": 10000,
                    "ENCRYPTION_MAX_BYTES": 2 * 1024**3,
                    "ENCRYPTION_MAX_OBJECT_BYTES": 256 * 1024**2,
                    "ENCRYPTION_PAGE_SIZE": 100,
                }
            )
            arguments.add_argument("--apply", action="store_true")
            arguments.add_argument("--confirm-writers-stopped", action="store_true")
            aliases = {
                "max_objects": "ENCRYPTION_MAX_OBJECTS",
                "max_bytes": "ENCRYPTION_MAX_BYTES",
                "max_object_bytes": "ENCRYPTION_MAX_OBJECT_BYTES",
                "page_size": "ENCRYPTION_PAGE_SIZE",
            }
            for flag in aliases:
                arguments.add_argument("--" + flag.replace("_", "-"))
        args = arguments.parse_args(argv)
        for flag, key in aliases.items():
            value = getattr(args, flag)
            if value is not None:
                args.set.append(key + "=" + value)
        try:
            values = resolve(defaults, SECRETS, args, os.environ, "OPS_CONFIG_FILE")
            require(
                not require_credentials
                or values["DATABASE_URL"]
                and values["S3_ACCESS_KEY"]
                and values["S3_SECRET_KEY"],
                "CONFIG_CREDENTIALS_REQUIRED",
            )
            endpoint(values["S3_ENDPOINT"])
            require(bool(values["S3_ENDPOINT"]), "CONFIG_ENDPOINT_INVALID")
            if "://" not in values["S3_ENDPOINT"]:
                values["S3_ENDPOINT"] = "http://" + values["S3_ENDPOINT"]
            require(
                re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", values["S3_BUCKET"])
                is not None
            )
            if mode == "maintenance":
                require(1 <= values["MAINTENANCE_INTERVAL_SECONDS"] <= 86400)
                require(0 <= values["MAINTENANCE_METRICS_PORT"] <= 65535)
                require(
                    re.fullmatch(
                        r"[A-Za-z0-9_.:-]{1,253}", values["MAINTENANCE_METRICS_HOST"]
                    )
                    is not None
                )
                path = text(values["MONITORING_STATUS_FILE"], 512)
                require(path.startswith("/") or Path(path).is_absolute())
            else:
                require(1 <= values["ENCRYPTION_MAX_OBJECTS"] <= 1000000)
                require(1 <= values["ENCRYPTION_PAGE_SIZE"] <= 1000)
                require(1 <= values["ENCRYPTION_MAX_OBJECT_BYTES"] <= 1024**3)
                require(1 <= values["ENCRYPTION_MAX_BYTES"] <= 1024**4)
                require(
                    not args.apply or args.confirm_writers_stopped,
                    "WRITERS_STOPPED_CONFIRMATION_REQUIRED",
                )
            return cls(
                values,
                mode,
                getattr(args, "apply", False),
                getattr(args, "confirm_writers_stopped", False),
            )
        except ConfigurationError:
            raise
        except (ValueError, TypeError, OverflowError):
            raise ConfigurationError("CONFIG_VALUE_INVALID") from None

    def snapshot(self):
        values = {**self.values, "MODE": self.mode}
        if self.mode == "encryption":
            values.update(
                APPLY=self.apply, CONFIRM_WRITERS_STOPPED=self.confirm_writers_stopped
            )
        return snapshot(values, SECRETS, {"S3_ENDPOINT"})

    def log_startup(self, service):
        visible, digest = self.snapshot()
        install_logging(service, VERSION)
        emit(
            service,
            VERSION,
            "configuration_loaded",
            stage="startup",
            effective_config=visible,
            config_hash=digest,
            stream=sys.stderr,
        )


def health(argv=None):
    """Silent health probe; use the same JSON/env/CLI port as maintenance."""
    from urllib.request import ProxyHandler, build_opener

    try:
        config = Config.load(argv, require_credentials=False)
        host = config["MAINTENANCE_METRICS_HOST"]
        host = "127.0.0.1" if host == "0.0.0.0" else host
        if ":" in host:
            host = "[" + host + "]"
        opener = build_opener(ProxyHandler({}))
        with opener.open(
            "http://"
            + host
            + ":"
            + str(config["MAINTENANCE_METRICS_PORT"])
            + "/healthz",
            timeout=3,
        ) as response:
            return (
                0 if response.status == 200 and response.read(257) == b"ready\n" else 1
            )
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(health(sys.argv[2:]) if sys.argv[1:2] == ["--health"] else 1)
