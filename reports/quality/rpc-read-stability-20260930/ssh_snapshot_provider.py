"""Fresh strict acceptance entrypoint; SSH/TLS verified, credentials memory-only.

Does not run on import. Each invocation requires a new output destination and
an explicit public deployment provenance file. No historical reports change.
"""
import argparse
import json
import ssl
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.run_fault_plaza_strict_acceptance import ORACLES, run_campaign
from scripts.verify_interview_demo import Client

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=15", "root@120.24.187.205"]


def remote(code):
    result = subprocess.run(SSH + ["python3 -"], input=code, capture_output=True,
                            text=True, encoding="utf-8", timeout=90)
    if result.returncode:
        # Do not echo remote command/exception streams; they can contain secrets.
        raise RuntimeError("Read-only SSH provider failed with exit " + str(result.returncode))
    return result.stdout


def authenticated_client():
    auth = json.loads(remote('''
import json, subprocess
from pathlib import Path
api = json.loads(subprocess.check_output(["docker", "inspect", "mini-drop-control-apiserver-1"]))[0]
if not api["State"]["Running"]:
    raise RuntimeError("API container not running")
env = dict(value.split("=", 1) for value in api["Config"]["Env"] if "=" in value)
cert_root = next(m["Source"] for m in api["Mounts"] if m["Destination"] == "/certs")
print(json.dumps({"key": env["MINI_DROP_API_KEY"], "ca": (Path(cert_root) / "ca.crt").read_text()}))
'''))
    client = Client("https://120.24.187.205", auth["key"])
    # Retain CA chain and hostname verification for the existing private CA;
    # Python 3.13+ default_context adds strict KU validation absent in that CA.
    client._context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client._context.load_verify_locations(cadata=auth["ca"])
    assert client._context.verify_mode == ssl.CERT_REQUIRED and client._context.check_hostname
    return client


def snapshot(lab):
    if lab not in {"python", "go", "java", "cpp"}:
        raise ValueError("Unknown lab")
    code = "LAB = " + repr(lab) + "\n" + SNAPSHOT_PROGRAM
    return json.loads(remote(code))


SNAPSHOT_PROGRAM = """
import json, subprocess
from pathlib import Path
name = "mini-drop-control-" + LAB + "-hotspot-1"
def identity():
    obj = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
    if not obj["State"]["Running"]:
        raise RuntimeError("lab not running")
    pid = obj["State"]["Pid"]
    proc = Path("/proc") / str(pid)
    # comm may include spaces or parentheses; numeric stat fields follow its last ')'.
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    status = (proc / "status").read_text().splitlines()
    nspid = next(line.split()[1:] for line in status if line.startswith("NSpid:"))
    return {"source": "host_proc_and_docker_inspect", "host_pid": pid,
            "namespace_pid": int(nspid[-1]), "start_ticks": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "container_id": obj["Id"], "container_started_at": obj["State"]["StartedAt"]}
before = identity()
worker_code = "import json; from server.app.drop_insight.fault_plaza import _request_json; print(json.dumps(_request_json('GET', '/snapshot', lab_key=" + repr(LAB) + ")))"
result = subprocess.run(["docker", "exec", "-i", "mini-drop-control-diagnosis-worker-1", "python", "-"],
                        input=worker_code, text=True, capture_output=True, timeout=40, check=True)
snapshot = json.loads(result.stdout)
after = identity()
if before != after or snapshot.get("pid") != after["namespace_pid"]:
    raise RuntimeError("lab lifetime changed while reading snapshot")
snapshot["acceptance_process_identity"] = after
print(json.dumps(snapshot))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", nargs="+", choices=sorted(ORACLES), action="extend")
    parser.add_argument("--deployment-json", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or (output.parent / (output.stem + "-cases")).exists():
        parser.error("Output or cases already exist; choose a fresh destination")
    provenance = json.loads(args.deployment_json.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or not provenance:
        parser.error("Deployment provenance must be a nonempty public metadata object")
    result = run_campaign(authenticated_client(), snapshot, output,
                          scenario_ids=args.scenario, deployment_provenance=provenance)
    print(json.dumps({key: result[key] for key in ("run_status", "completed_count", "passed_count", "failed_count")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
