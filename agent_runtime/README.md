# Isolated AgentScope runtime

This service runs the official `agentscope==2.0.10` SDK. Its `Agent`, native
ReAct loop, `Toolkit`, custom `ToolBase` implementations, `DeepSeekChatModel`,
message history and model/acting middleware perform planning, diagnosis and
repair orchestration. It does not implement a replacement model/tool loop.

The stable wire contract is `contracts/agent-runtime-v1.json`. Go owns every
business tool, target identity, consent, authorization, persistence, lease,
attempt fence, budget and final result acceptance. The runner has no database,
object-store, collector, Docker or code executor access. The only registered
tools are the phase's Go-provided business schemas; shell, file, network, MCP
and context compression tools are not enabled.

Run as UID/GID `10002:10002` with supplemental group `10003`, a read-only root
filesystem, all capabilities dropped and `no-new-privileges`. Its socket
directory is owned by UID 10002, group 10003, mode 2750; the gateway directory
is owned by UID 10001, group 10003, mode 2750. The consumer mount of each
directory must be read-only. Socket mode is 0660. Both servers and the runner's
gateway client verify `SO_PEERCRED`. `GET /health` also permits the runner's own
UID for a local container probe; `/v1/run` only permits the configured Go UID.

Trusted configuration:

- `AI_ENDPOINT`: the existing HTTPS chat-completions endpoint, including
  `/chat/completions`; the runner derives the SDK base URL.
- `AI_MODEL`: the existing server-selected DeepSeek model. A run naming a
  different model is rejected.
- `AI_API_KEY_FILE`: read-only server secret, default `/run/secrets/ai_api_key`.
- `AGENTSCOPE_RUNNER_SOCKET`: default `/run/drop-ai-runner/control.sock`.
- `AGENTSCOPE_GATEWAY_SOCKET`: default `/run/drop-ai-tools/control.sock`.
- `AGENTSCOPE_PEER_UID`: Go UID, default `10001`.
- `AGENTSCOPE_SOCKET_GROUP`: socket directory GID, default `10003`; startup
  requires its own directory to be a real directory owned by the runtime UID,
  with this GID and mode 2750. Consumer write access and symlinks are rejected.
- `AGENTSCOPE_MAX_CONCURRENT`: 1–16, default `4`; excess runs fail immediately.
- `OTEL_SDK_DISABLED=true`: disable telemetry export in deployment.

The default SDK retry layers are both disabled. Model requests and responses,
UDS headers/bodies, schemas, arguments and outputs are bounded. HTTP redirects,
proxy environment variables and compressed provider responses are disabled.
Provider and SDK content logging is disabled. Hidden thought is discarded
before the Agent receives its model response. Runtime metadata is derived
from the imported distribution, instantiated class and observed callbacks;
`tool_calls` counts actual runner-to-Go tool callbacks, excluding initial
evidence that Go placed in the request. Go independently compares callback
counts and the accepted JSON result.

The accepted `result` is validated as JSON, then forwarded as its original
opaque JSON value. Numeric tokens such as `0.000001`, `1e-6`, `-0` and decimals
beyond float64 precision are never parsed and re-encoded for the final result.
Go's strict final content comparison remains unchanged.

Private non-200 responses may include `diagnostics` containing only a fixed
stage enum, a fixed exception-kind enum and model/tool call counters. They
contain no exception text, class names, traceback, request or provider content,
credential or capability. Go validates these fields separately and keeps them
out of reports and browser responses. Successful response fields are unchanged.

Go disconnection, deadline and shutdown cancel the framework task and close
pending model and callback I/O. Recovery starts a new bounded persisted phase;
this service stores no conversations or capabilities on disk.

Use Python 3.12 and install the verified dependency lock before running
regression tests from the repository root (prefer a dedicated virtual environment):

```text
python -m pip install --only-binary=:all: --require-hashes -r agent_runtime/requirements.lock
python -m pip check
python -m unittest discover -s agent_runtime/tests -v
```

The tests use explicitly imported synthetic provider/tool fixtures, never a
live model or production task. Linux additionally tests actual Unix sockets,
peer UID rejection, disconnect cancellation and callback cancellation.

Upstream sources:

- [AgentScope 2.0.10 tagged source](https://github.com/agentscope-ai/agentscope/tree/v2.0.10),
  commit `e6c014f0be49b2a6f6f62a9453082a0b975484e1`.
- [PyPI release](https://pypi.org/project/agentscope/2.0.10/).
