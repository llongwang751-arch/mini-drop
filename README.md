# Mini-Drop

Mini-Drop is a compact, runnable performance-diagnostics platform containing a Web UI, control-plane Server, remote Agent, pluggable collectors, and Analyzer.

The Web UI is implemented with React 19, Vite, and Phosphor Icons. The Server, Agent, collectors, and Analyzer remain Python 3.12 components.

## Quick start

Requirements: Docker Engine with Compose, Linux kernel 5.4+, and root/privileged-container permission for eBPF. The basic `/proc` demo works without perf/eBPF kernel permission.

```bash
docker compose up --build
make demo
```

Open <http://localhost:8080>. The Agent uses the host PID namespace, so enter a host PID visible from the Agent. `PID 1` is suitable for the demo.

Without Docker, run `python scripts/run_local.py` and then `python scripts/demo.py`.

For frontend development, run `npm install && npm run dev` in `frontend/`. Vite proxies `/api` requests to the Python Server on port 8080.

## What is implemented

- Strict persisted state machine: `PENDING -> RUNNING -> UPLOADING -> DONE / FAILED`; every transition requires a reason.
- Agent heartbeat every 5 seconds; Server marks it offline after 30 seconds and audits offline/recovery events.
- Pluggable CPU/perf-semantics, real bpftrace kernel tracepoint, and py-spy language-stack collectors.
- Automatic degradation to `/proc` when optional tools or kernel capabilities are unavailable, with the reason exposed in results.
- CPU, RSS, and I/O samples; flame graph, TopN, eBPF distribution, and verifiable rule-based attribution.
- Continuous profiling by automatic time-slice creation.
- Natural-language collection planning that extracts a verifiable PID, collector, duration, rate, and Agent.
- Structured logs, explicit errors, SQLite persistence, responsive Web UI, unit tests, and three end-to-end tests.

## Useful commands

```bash
make test
pip install -r requirements-dev.txt && make coverage
docker compose up --build
make demo
```

For a visible eBPF `kprobe:vfs_write` change, create an `ebpf` profile in the UI while running:

```bash
dd if=/dev/zero of=/tmp/minidrop-io.bin bs=1M count=512
```

The Agent image runs privileged because bpftrace needs access to kernel tracing. Production deployments should grant only the required capabilities and use an allow-listed probe catalog.

## API summary

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/tasks` | Create a profile |
| `GET` | `/api/tasks` | List profiles |
| `GET` | `/api/tasks/{id}` | Profile, transitions, and analysis |
| `GET` | `/api/agents` | Agent inventory |
| `GET` | `/api/audit` | Offline/recovery audit |
| `GET` | `/api/continuous/{agent_id}` | Last five minutes of continuous slices |
| `POST` | `/api/natural-language` | Parse a sentence and create a collection task |
| `POST` | `/api/tasks/{id}/stop-continuous` | Stop successor slices for a continuous profile |
| `POST` | `/api/agents/{id}/heartbeat` | Agent heartbeat |
| `POST` | `/api/agents/{id}/claim` | Atomically claim work |
| `POST` | `/api/tasks/{id}/upload` | Upload raw data and analyze |
| `POST` | `/api/tasks/{id}/fail` | Persist explicit failure |

Review documents:

- [DESIGN.md](DESIGN.md): architecture, state machine, decisions, tradeoffs, and AI collaboration.
- [ASSESSMENT.md](ASSESSMENT.md): requirement-by-requirement acceptance evidence and honest limitations.
- [DEMO.md](DEMO.md): a ready-to-record 15-minute demonstration script.
