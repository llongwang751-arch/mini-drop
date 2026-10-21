"""启动两个独立本机进程并执行真实HTTP场景；这不是Docker、生产或模型验收。"""
from datetime import timedelta
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from load import get_json, run_load, signals
from server import stamp, utcnow


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def wait_health(origin, child):
    for _ in range(100):
        if child.poll() is not None:
            raise RuntimeError("DEMO_PROCESS_EXITED")
        try:
            status,body=get_json(origin+"/health",timeout=.2)
            if status==200:return body
        except (OSError,ValueError):
            pass
        time.sleep(.05)
    raise RuntimeError("DEMO_HEALTH_TIMEOUT")


def verify(output):
    if output.exists():
        raise ValueError("NEW_OUTPUT_DIRECTORY_REQUIRED")
    output.mkdir(parents=True)
    source=Path(__file__).resolve().parent
    children=[]
    try:
        with tempfile.TemporaryDirectory(prefix="drop-live-observability-") as folder:
            catalog_port,gateway_port=free_port(),free_port()
            catalog_origin=f"http://127.0.0.1:{catalog_port}"
            gateway_origin=f"http://127.0.0.1:{gateway_port}"
            processes={}
            for role,port in (("catalog",catalog_port),("gateway",gateway_port)):
                argv=[sys.executable,str(source/"server.py"),"--role",role,"--host","127.0.0.1","--port",str(port),"--database",str(Path(folder)/"catalog.sqlite"),"--downstream",catalog_origin]
                kwargs={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
                child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
                children.append(child)
                health=wait_health(catalog_origin if role=="catalog" else gateway_origin,child)
                processes[role]={"separate_process":health["pid"]==child.pid,"uid":health["uid"],"runtime_revision":health["runtime_revision"]}
            started=stamp(utcnow()-timedelta(seconds=1))
            empty_end=stamp(utcnow()-timedelta(days=1))
            empty_start=stamp(utcnow()-timedelta(days=1,seconds=5))
            assert signals(gateway_origin,"gateway","metrics",empty_start,empty_end)["metrics"]==[]
            phases=[]
            for scenario,count in (("normal",6),("slow_query",6),("cpu",6),("upstream_error",4),("normal",6)):
                phase=run_load(gateway_origin,scenario,count)
                phase["signals"]={role:{signal:signals(origin,role,signal,phase["start"],phase["end"]) for signal in ("metrics","logs","traces")} for role,origin in (("gateway",gateway_origin),("catalog",catalog_origin))}
                phases.append(phase)
            p95=lambda phase: next(row["value"] for row in phase["signals"]["gateway"]["metrics"]["metrics"] if row["name"]=="latency_p95_ms")
            assert p95(phases[1])>p95(phases[0])*5
            for phase in phases:
                expected=502 if phase["scenario"]=="upstream_error" else 200
                assert all(row["status_code"]==expected for row in phase["client_responses"])
                traces=phase["signals"]["gateway"]["traces"]["traces"]
                downstream=phase["signals"]["catalog"]["traces"]["traces"]
                assert {row["trace_id"] for row in traces}=={row["trace_id"] for row in downstream}
                for role in ("gateway","catalog"):
                    for metric in phase["signals"][role]["metrics"]["metrics"]:
                        assert phase["start"]<=metric["observed_at"]<=phase["end"]
            result={"passed":True,"provenance":"synthetic_controlled_live_http_demo","execution":"two_separate_local_processes_not_container_acceptance",
                    "model_requests":0,"production_requests":0,"processes":processes,"empty_window_metrics":[],
                    "normal_before_p95_ms":p95(phases[0]),"slow_query_p95_ms":p95(phases[1]),"cpu_p95_ms":p95(phases[2]),
                    "normal_after_p95_ms":p95(phases[-1]),"requests":sum(phase["completed_requests"] for phase in phases),
                    "shared_trace_ids_verified":True,"phase_windows_verified":True,"phases":phases,
                    "changes":{role:signals(origin,role,"changes",started,stamp(utcnow())) for role,origin in (("gateway",gateway_origin),("catalog",catalog_origin))},
                    "source_sha256":{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ("server.py","load.py")}}
            (output/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print(json.dumps({key:result[key] for key in ("passed","execution","requests","normal_before_p95_ms","slow_query_p95_ms","cpu_p95_ms","normal_after_p95_ms","shared_trace_ids_verified","phase_windows_verified")}),flush=True)
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    verify(parser.parse_args().output.resolve())
