"""仅限可销毁测试环境的本地HS256 issuer，不冒充GoTrue或任何真实外部认证商。

真实CRM代码会通过HTTP调用/auth/v1/user；适配器验证签名、有效期、受众、issuer、
subject及本地fixture会话绑定。组织/角色不从JWT返回值授予，而由真实PG实时读取。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

ISSUER = "urn:drop:crm-quality:local"
AUDIENCE = "drop-crm-quality"
MAX_TOKEN_BYTES = 4096


def _encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(text):
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("LOCAL_JWT_ENCODING")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("LOCAL_JWT_DUPLICATE_KEY")
        value[key] = item
    return value


def issue(secret, role, *, user=None, session=None, expires_in=600, now=None):
    now = int(time.time()) if now is None else now
    claims = {
        "role": role,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now - 2,
        "exp": now + expires_in,
    }
    if user is not None:
        claims.update(sub=str(user), session_id=str(session), aal="aal1")
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = _encode(json.dumps(claims, separators=(",", ":")).encode())
    unsigned = header + "." + payload
    return (
        unsigned
        + "."
        + _encode(hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).digest())
    )


def verify(token, secret, *, users, now=None):
    if not isinstance(token, str) or not 1 <= len(token.encode()) <= MAX_TOKEN_BYTES:
        raise ValueError("LOCAL_JWT_SIZE")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("LOCAL_JWT_PARTS")
    header = json.loads(_decode(parts[0]), object_pairs_hook=_unique)
    if header != {"alg": "HS256", "typ": "JWT"}:
        raise ValueError("LOCAL_JWT_ALGORITHM")
    signature = _decode(parts[2])
    expected = hmac.new(
        secret.encode(), (parts[0] + "." + parts[1]).encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("LOCAL_JWT_SIGNATURE")
    claims = json.loads(_decode(parts[1]), object_pairs_hook=_unique)
    now = int(time.time()) if now is None else now
    if (
        not isinstance(claims, dict)
        or claims.get("iss") != ISSUER
        or claims.get("aud") != AUDIENCE
        or claims.get("role") != "authenticated"
    ):
        raise ValueError("LOCAL_JWT_CLAIMS")
    if (
        type(claims.get("exp")) is not int
        or type(claims.get("nbf")) is not int
        or not claims["nbf"] <= now < claims["exp"]
    ):
        raise ValueError("LOCAL_JWT_EXPIRY")
    sub, session = claims.get("sub"), claims.get("session_id")
    try:
        if str(UUID(sub)) != sub or str(UUID(session)) != session:
            raise ValueError("LOCAL_JWT_ID")
    except (TypeError, AttributeError):
        raise ValueError("LOCAL_JWT_ID") from None
    user = users.get(sub)
    if not user or user.get("session_id") != session or user.get("revoked"):
        raise ValueError("LOCAL_JWT_FIXTURE_SESSION")
    return claims, {"id": sub, "factors": user.get("factors", [])}
