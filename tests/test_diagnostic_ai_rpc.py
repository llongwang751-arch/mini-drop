import grpc
import pytest

from server.app.diagnostic_ai_rpc import DiagnosticAIService
from server.app.generated import diagnostic_ai_pb2


class AbortedRPC(RuntimeError):
    pass


class FakeContext:
    def __init__(self, metadata=()):
        self._metadata = metadata
        self.code = None

    def invocation_metadata(self):
        return self._metadata

    def abort(self, code, details):
        self.code = code
        raise AbortedRPC(details)


def test_private_diagnostic_rpc_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("MINI_DROP_GRPC_AUTH_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_GRPC_TOKEN", "expected-token")
    context = FakeContext((("x-mini-drop-grpc-token", "wrong-token"),))

    with pytest.raises(AbortedRPC, match="invalid internal gRPC token"):
        DiagnosticAIService().Invoke(diagnostic_ai_pb2.DiagnosticAIRequest(), context)

    assert context.code == grpc.StatusCode.UNAUTHENTICATED
