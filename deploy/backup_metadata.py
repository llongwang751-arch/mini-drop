"""在受保护的备份 manifest 中保存有界 S3 对象元数据。

恢复 PUT 只使用内容元数据，不能复用传输、鉴权或加密响应头；
加密由恢复目标 bucket 的策略提供，不能从旧响应头盲目重放。
"""

import re
from collections.abc import Mapping

OBJECT_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-disposition",
        "content-language",
    }
)
_USER_HEADER = re.compile(r"x-amz-meta-[a-z0-9!#$%&'*+.^_`|~-]+\Z")
_MAX_BYTES = 8192
_MAX_FIELDS = 128


def validate_metadata(value):
    """恢复备份中的元数据不可信，必须在任何 PUT 前校验字段白名单、类型与字节预算。"""
    if value is None:
        return {}  # 兼容尚未保存对象元数据的旧备份。
    if not isinstance(value, Mapping) or len(value) > _MAX_FIELDS:
        raise ValueError("Invalid or oversized object metadata")
    result = {}
    size = 0
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("Object metadata must contain string headers")  # noqa: TRY004 - 损坏备份统一使用一种校验错误类型
        normalized = key.lower()
        if (
            normalized in result
            or normalized not in OBJECT_HEADERS
            and not _USER_HEADER.fullmatch(normalized)
        ):
            raise ValueError("Invalid or duplicate object metadata header")
        if any(ord(char) < 32 or ord(char) == 127 for char in item):
            raise ValueError("Invalid object metadata value")
        size += len(normalized.encode("utf-8")) + len(item.encode("utf-8"))
        if size > _MAX_BYTES:
            raise ValueError("Object metadata exceeds the backup budget")
        result[normalized] = item
    return result


def capture_metadata(headers):
    """只提取内容相关元数据，不保留 SSE、鉴权或其他响应头。"""
    selected = {}
    seen = set()
    for key, value in headers.items():
        if not isinstance(key, str):
            raise ValueError("Invalid S3 response header")  # noqa: TRY004 - 元数据校验失败保持统一类型
        normalized = key.lower()
        if normalized in OBJECT_HEADERS or normalized.startswith("x-amz-meta-"):
            if normalized in seen:
                raise ValueError("Duplicate object metadata response header")
            seen.add(normalized)
            selected[normalized] = value
    return validate_metadata(selected)


def verify_metadata(expected, headers):
    """比较预期备份元数据与恢复后实际 HTTP 响应，字段类型和大小同样必须通过白名单校验。"""
    if validate_metadata(expected) != capture_metadata(headers):
        raise ValueError("Isolated S3 restore metadata mismatch")
