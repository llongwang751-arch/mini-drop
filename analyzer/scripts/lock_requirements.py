"""Pin the installed Linux runtime to hashes from canonical PyPI metadata."""

from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
from pathlib import Path
import re
import urllib.request


def package_lock(distribution):
    name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
    version = distribution.version
    if not re.fullmatch(r"[a-z0-9-]+", name) or not re.fullmatch(
        r"[a-zA-Z0-9.+-]+", version
    ):
        raise ValueError("Distribution identity cannot be locked")
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{name}/{version}/json", timeout=30
    ) as response:
        metadata = json.load(response)
    hashes = sorted({item["digests"]["sha256"] for item in metadata["urls"]})
    if not hashes or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes):
        raise ValueError("Canonical release hashes are missing")
    marker = '; sys_platform == "linux"' if name == "memray" else ""
    return name, f"{name}=={version}{marker} \\\n" + " \\\n".join(
        "    --hash=sha256:" + value for value in hashes
    )


def main():
    distributions = [
        distribution
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"].lower() not in {"pip", "setuptools", "wheel"}
    ]
    with ThreadPoolExecutor(max_workers=6) as executor:
        records = sorted(executor.map(package_lock, distributions))
    target = Path(__file__).resolve().parents[1] / "requirements.lock"
    target.write_text(
        "# Linux runtime lock. Release hashes verified against https://pypi.org/pypi/{name}/{version}/json\n"
        + "\n\n".join(value for _, value in records)
        + "\n",
        encoding="utf8",
    )
    print(
        json.dumps(
            {"locked_distributions": len(records), "bytes": target.stat().st_size}
        )
    )


if __name__ == "__main__":
    main()
