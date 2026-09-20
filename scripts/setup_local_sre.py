"""Create a separate local-only environment; never print provider credentials."""
from pathlib import Path
import getpass
import json
import secrets
import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / ".env.local-sre"
    if path.exists():
        print("Existing .env.local-sre retained.")
        return
    key = getpass.getpass("SiliconFlow API key (hidden): ")
    response = requests.get("https://api.siliconflow.cn/v1/models", headers={"Authorization": "Bearer " + key}, timeout=15)
    if response.status_code != 200:
        raise SystemExit("Model discovery failed: HTTP " + str(response.status_code))
    ids = {row["id"] for row in response.json().get("data", [])}
    preferred = ["deepseek-ai/DeepSeek-V3.2", "deepseek-ai/DeepSeek-V3.1", "deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-32B"]
    model = next((name for name in preferred if name in ids), None)
    if not model:
        raise SystemExit("No supported local smoke-test chat model found.")
    settings = dict(MINI_DROP_ENV="dev", POSTGRES_DB="mini_drop", POSTGRES_USER="mini_drop",
        POSTGRES_PASSWORD=secrets.token_hex(16), MINIO_ACCESS_KEY="mini_drop", MINIO_SECRET_KEY=secrets.token_hex(16),
        MINIO_ENDPOINT="minio:9000", MINIO_AGENT_ENDPOINT="minio:9000", MINIO_BUCKET="mini-drop",
        MINI_DROP_API_AUTH_ENABLED="0", MINI_DROP_GRPC_AUTH_ENABLED="0", MINI_DROP_GRPC_SECURE="0",
        NATIVE_AGENT_ID="local-sre-native", MINI_DROP_AI_ENABLED="full", MINI_DROP_AI_PROVIDER="siliconflow",
        MINI_DROP_AI_BASE_URL="https://api.siliconflow.cn/v1", MINI_DROP_AI_API_KEY=key, MINI_DROP_AI_MODEL=model,
        SILICONFLOW_API_KEY=key, MINI_DROP_AGENT_FRAMEWORK="langgraph", MINI_DROP_AGENT_CHECKPOINT_BACKEND="postgres",
        MINI_DROP_AGENT_MODEL_TIMEOUT_SEC="90", MINI_DROP_SILICONFLOW_ENABLE_THINKING="false")
    settings["DATABASE_URL"] = "postgresql://mini_drop:" + settings["POSTGRES_PASSWORD"] + "@postgres:5432/mini_drop"
    # python SQLAlchemy expects psycopg3; the Go/Native processes normalize this scheme.
    settings["DATABASE_URL"] = settings["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    path.write_text("\n".join(f"{name}={value}" for name, value in settings.items()) + "\n", encoding="utf-8")
    print(json.dumps({"configured": True, "model": model, "environment": path.name}))


if __name__ == "__main__":
    main()
