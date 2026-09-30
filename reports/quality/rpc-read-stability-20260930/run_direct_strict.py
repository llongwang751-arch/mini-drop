"""Fresh campaign on the frozen deployment, with explicit verified direct HTTP."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.verify_interview_demo import Client
from scripts.run_fault_plaza_strict_acceptance import run_campaign

spec = importlib.util.spec_from_file_location(
    "provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_name(args.output.stem + "-cases").exists():
        parser.error("Choose a new output; historical evidence is immutable")
    trusted = provider.authenticated_client()
    client = Client(trusted.base, trusted._key, proxy_mode="direct")
    client._context = trusted._context
    assert client._context.check_hostname
    assert client._context.verify_mode.name == "CERT_REQUIRED"
    provenance = json.loads((ROOT / "output/acceptance/java-scope-20260930/deployment.json").read_text())
    provenance["acceptance_transport"] = {
        "proxy_mode": "direct", "tls_certificate_verified": True,
        "tls_hostname_verified": True,
        "client_source_sha256": hashlib.sha256((ROOT / "scripts/verify_interview_demo.py").read_bytes()).hexdigest(),
        "provider_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    result = run_campaign(client, provider.snapshot, args.output,
                          scenario_ids=["java-gc-pressure"], deployment_provenance=provenance)
    print(json.dumps({key: result[key] for key in ("run_status", "completed_count", "passed_count", "failed_count")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
