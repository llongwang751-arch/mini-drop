import hashlib
import io
from pathlib import Path

from minio import Minio
from minio.error import S3Error
import urllib3

from .config import Config
from .errors import AnalysisError


class ObjectStore:
    def __init__(self, config: Config):
        self.bucket = config.s3_bucket
        self.client = Minio(
            config.s3_endpoint,
            access_key=config.s3_access_key,
            secret_key=config.s3_secret_key,
            secure=config.s3_secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5, read=30), retries=False
            ),
        )

    def ready(self) -> bool:
        return self.client.bucket_exists(self.bucket)

    def download(
        self,
        key: str,
        destination: Path,
        *,
        expected_size: int,
        max_bytes: int,
        before_write=None,
    ) -> bytes:
        if expected_size <= 0 or expected_size > max_bytes:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Artifact metadata exceeds input size limit"
            )
        response = None
        try:
            response = self.client.get_object(self.bucket, key)
            downloaded = 0
            with destination.open("xb") as stream:
                # Integrity metadata covers exact object bytes. S3 may preserve
                # Content-Encoding; HTTP decompression would change those bytes.
                for chunk in response.stream(64 * 1024, decode_content=False):
                    downloaded += len(chunk)
                    if downloaded > max_bytes or downloaded > expected_size:
                        raise AnalysisError(
                            "INPUT_LIMIT_EXCEEDED",
                            "Downloaded artifact exceeds declared size or input limit",
                        )
                    if before_write is not None:
                        before_write(len(chunk))
                    stream.write(chunk)
            if downloaded != expected_size:
                raise AnalysisError(
                    "ARTIFACT_INTEGRITY_ERROR",
                    "Downloaded artifact has an unexpected size",
                )
            return destination.read_bytes()
        except S3Error as exc:
            raise AnalysisError(
                "STORAGE_ERROR",
                "Object storage could not return the raw artifact",
                retryable=exc.code
                not in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"},
            ) from exc
        except (OSError, urllib3.exceptions.HTTPError) as exc:
            raise AnalysisError(
                "STORAGE_ERROR", "Raw artifact download failed", retryable=True
            ) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def upload(self, key: str, body: bytes, content_type: str) -> dict:
        digest = hashlib.sha256(body).hexdigest()
        try:
            self.client.put_object(
                self.bucket,
                key,
                io.BytesIO(body),
                len(body),
                content_type=content_type,
                metadata={"sha256": digest},
            )
        except (S3Error, OSError, urllib3.exceptions.HTTPError) as exc:
            raise AnalysisError(
                "STORAGE_ERROR", "Analysis artifact upload failed", retryable=True
            ) from exc
        return {
            "object_key": key,
            "size": len(body),
            "sha256": digest,
            "content_type": content_type,
        }
