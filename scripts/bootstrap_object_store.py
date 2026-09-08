"""Create the runtime object-store bucket after infrastructure is healthy."""

from __future__ import annotations

import json
import os

from server.app.storage import ensure_bucket


def main() -> None:
    bucket = os.getenv("MINIO_BUCKET", "mini-drop").strip()
    ensure_bucket(bucket)
    print(json.dumps({"event": "object_store_bucket_ready", "bucket": bucket}))


if __name__ == "__main__":
    main()
