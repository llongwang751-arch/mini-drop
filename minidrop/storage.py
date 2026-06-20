import json
import os
import time
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put_json(self, key: str, value: dict) -> str:
        ...

    def get_json(self, key: str) -> dict:
        ...


class LocalObjectStore:
    def __init__(self, root: str = "data/objects"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_json(self, key: str, value: dict) -> str:
        path = self.root / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return key

    def get_json(self, key: str) -> dict:
        return json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))


class MinioObjectStore:
    def __init__(self):
        from minio import Minio

        endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "minidrop")
        secret_key = os.getenv("MINIO_SECRET_KEY", "minidrop-secret")
        secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        self.bucket = os.getenv("MINIO_BUCKET", "minidrop")
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        for attempt in range(30):
            try:
                if not self.client.bucket_exists(self.bucket):
                    self.client.make_bucket(self.bucket)
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(2)

    def put_json(self, key: str, value: dict) -> str:
        import io

        data = json.dumps(value).encode("utf-8")
        self.client.put_object(self.bucket, f"{key}.json", io.BytesIO(data), length=len(data), content_type="application/json")
        return key

    def get_json(self, key: str) -> dict:
        response = self.client.get_object(self.bucket, f"{key}.json")
        try:
            return json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
            response.release_conn()


def create_object_store() -> ObjectStore:
    backend = os.getenv("OBJECT_STORAGE", "local").lower()
    if backend == "minio":
        return MinioObjectStore()
    return LocalObjectStore(os.getenv("LOCAL_OBJECT_ROOT", "data/objects"))
