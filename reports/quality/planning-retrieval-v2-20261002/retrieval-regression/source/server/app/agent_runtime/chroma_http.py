"""Bound Chroma 1.5.x HTTP I/O before startup and validation requests."""
from functools import lru_cache

import httpx
from chromadb.api.client import Client
from chromadb.config import Settings


class BoundedClient(Client):
    def get_user_identity(self):
        self._server._session.timeout = httpx.Timeout(5.0, connect=2.0)
        return super().get_user_identity()

    def _validate_tenant_database(self, tenant, database):
        self._admin_client._server._session.timeout = httpx.Timeout(5.0, connect=2.0)
        return super()._validate_tenant_database(tenant, database)


@lru_cache(maxsize=8)
def bounded_http_client(host: str, port: int, ssl: bool):
    # This small version-pinned adapter avoids a global HTTP monkey patch.
    return BoundedClient(settings=Settings(chroma_api_impl="chromadb.api.fastapi.FastAPI",
        chroma_server_host=host, chroma_server_http_port=port,
        chroma_server_ssl_enabled=ssl, anonymized_telemetry=False))
