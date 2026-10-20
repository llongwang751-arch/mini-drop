"""Actual AgentScope SDK with an imported in-memory provider fixture only."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx

from drop_agent_runtime.config import Config
from drop_agent_runtime.runner import run_agent as actual_run_agent
from drop_agent_runtime.server import serve


def completion(*, text=None, calls=None):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for identity, name, args in calls
        ]
    return {
        "id": "imported-interop-provider-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop", "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def diagnosis_report(reference="E2"):
    return json.dumps({
        "summary": "Imported SDK and Go UDS interoperability fixture.",
        "hypotheses": [{"claim": "Imported hypothesis only.", "evidence_ids": ["E1", reference], "verification": "Verify with an independent controlled measurement."}],
        "limitations": ["Synthetic provider and tools; no production measurement."],
    })


async def fixture_run(request, config, gateway):
    scenario = request.messages[0]["content"].removeprefix("imported-interop:")
    call = 0
    async def provider(incoming):
        nonlocal call
        call += 1
        if scenario == "cancel":
            marker = Path(os.environ["INTEROP_MARKER_DIR"])
            (marker / "provider_started").write_text("imported fixture started\n", encoding="utf-8")
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                (marker / "provider_canceled").write_text("actual provider task canceled\n", encoding="utf-8")
                raise
        if scenario == "planning":
            response = completion(calls=[("planning-list", "list_collectors", {})]) if call == 1 else completion(calls=[("planning-propose", "propose_collection", {"kind": "PERF_CPU", "duration_seconds": 4, "frequency_hz": 49, "rationale": "Imported terminal planning fixture."})])
        elif scenario in ("repair", "numeric_roundtrip"):
            if call == 1:
                response = completion(calls=[("repair-profile", "profile_business_flow", {}), ("repair-source", "read_source", {"path": "fixture.py", "start_line": 1, "end_line": 40})])
            elif call == 2:
                response = completion(calls=[("repair-test", "test_patch", {"edits": [{"path": "fixture.py", "sha256": "a" * 64, "old": "imported", "new": "fixture"}]})])
            else:
                response = completion(text=json.dumps({"summary": "Imported isolated repair fixture.", "evidence_ids": ["S1", "S2", "S3"], "locations": [{"path": "fixture.py", "line": 31, "reason": "Imported observed hotspot."}]}))
        elif scenario == "budget":
            response = completion(calls=[("over-1", "inspect_hotspots", {}), ("over-2", "check_collection_quality", {}), ("over-3", "inspect_call_paths", {"rank": 1})])
        elif call == 1:
            response = completion(calls=[("diagnosis-hotspots", "inspect_hotspots", {}), ("diagnosis-quality", "check_collection_quality", {})])
        elif scenario == "format_retry" and call < 5:
            response = completion(text=diagnosis_report("E999"))
        else:
            response = completion(text=diagnosis_report())
        return httpx.Response(200, json=response)
    return await actual_run_agent(request, config, gateway, transport=httpx.MockTransport(provider))


if __name__ == "__main__":
    config = Config(
        os.environ["INTEROP_RUNNER_SOCKET"],
        os.environ["INTEROP_GATEWAY_SOCKET"],
        10001,
        "https://imported-provider.invalid",
        "deepseek-flash",
        os.environ["INTEROP_FIXTURE_KEY_FILE"],
    )
    with patch("drop_agent_runtime.server.run_agent", fixture_run):
        asyncio.run(serve(config))
