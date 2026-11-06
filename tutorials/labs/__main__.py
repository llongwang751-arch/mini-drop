"""Run fixed teaching cases without credentials, services or external models."""
import argparse
import json
from . import analysis, diagnosis, lifecycle


def cases():
    result = {}
    for module in (lifecycle, analysis, diagnosis):
        for name, handler in module.CASES.items():
            if name in result:
                raise ValueError(f"Duplicate teaching case: {name}")
            result[name] = handler
    return result


def run(selected):
    outcomes = []
    available = cases()
    for name in selected:
        try:
            payload = available[name]()
            checks = payload.get("checks")
            if not isinstance(checks, list) or not checks:
                raise ValueError("Teaching case returned no assertions")
            if any(item.get("passed") is not True for item in checks):
                raise AssertionError("Teaching case contains a failed assertion")
            # Reject non-JSON or non-finite observations before reporting success.
            json.dumps(payload, allow_nan=False)
            outcomes.append({"name": name, "passed": True, **payload})
        except Exception as error:
            outcomes.append({"name": name, "passed": False,
                             "error": type(error).__name__, "message": str(error)[:200]})
    return {
        "scope": "fixed synthetic teaching inputs; temporary SQLite is not PostgreSQL validation; no collection or external model calls",
        "passed": all(item["passed"] for item in outcomes),
        "case_count": len(outcomes),
        "check_count": sum(len(item.get("checks", [])) for item in outcomes),
        "cases": outcomes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", choices=sorted(cases()))
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result")
    args = parser.parse_args(argv)
    available = cases()
    if not args.case and not args.all:
        if args.json:
            print(json.dumps({"cases": list(available)}, ensure_ascii=False))
        else:
            print("Drop 教学实验（固定合成输入，不连接生产服务）")
            for name in available:
                print(f"  {name}")
        return 0
    result = run(list(available) if args.all else [args.case])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        for item in result["cases"]:
            state = "PASS" if item["passed"] else "FAIL"
            print(f"{state} {item['name']}: {item.get('summary', item.get('message', ''))}")
        print(f"cases={result['case_count']} checks={result['check_count']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
