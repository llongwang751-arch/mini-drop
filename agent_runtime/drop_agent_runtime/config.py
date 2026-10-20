"""Trusted process configuration, never populated from model input."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class Config:
    socket_path: str
    callback_socket_path: str
    peer_uid: int
    endpoint: str
    model: str
    api_key_file: str
    max_concurrent: int = 4
    socket_group: int = 10003

    @classmethod
    def from_env(cls) -> "Config":
        endpoint = os.environ.get("AI_ENDPOINT", "")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith("/chat/completions")
        ):
            raise ValueError("invalid trusted model endpoint")
        model = os.environ.get("AI_MODEL", "")
        if not model or len(model) > 128 or any(ord(c) < 32 for c in model):
            raise ValueError("invalid trusted model name")
        return cls(
            socket_path=os.environ.get(
                "AGENTSCOPE_RUNNER_SOCKET",
                os.environ.get("AGENTSCOPE_SOCKET", "/run/drop-ai-runner/control.sock"),
            ),
            callback_socket_path=os.environ.get(
                "AGENTSCOPE_GATEWAY_SOCKET",
                os.environ.get(
                    "AGENTSCOPE_CALLBACK_SOCKET", "/run/drop-ai-tools/control.sock"
                ),
            ),
            peer_uid=int(os.environ.get("AGENTSCOPE_PEER_UID", "10001")),
            endpoint=urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path[: -len("/chat/completions")],
                    "",
                    "",
                )
            ),
            model=model,
            api_key_file=os.environ.get("AI_API_KEY_FILE", "/run/secrets/ai_api_key"),
            max_concurrent=int(os.environ.get("AGENTSCOPE_MAX_CONCURRENT", "4")),
            socket_group=int(os.environ.get("AGENTSCOPE_SOCKET_GROUP", "10003")),
        )

    def read_api_key(self) -> str:
        with Path(self.api_key_file).open("rb") as secret:
            raw = secret.read(4097)
        if len(raw) > 4096:
            raise ValueError("model credential exceeds limit")
        key = raw.decode("utf-8").strip()
        if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError("invalid model credential")
        return key
