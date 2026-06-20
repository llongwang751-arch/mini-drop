from fastapi import Request

from ..db import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_current_user(request: Request) -> dict:
    return {
        "uid": request.headers.get("X-MiniDrop-User", "demo-user"),
        "name": request.headers.get("X-MiniDrop-User-Name", "Demo User"),
        "groups": [item.strip() for item in request.headers.get("X-MiniDrop-Groups", "default").split(",") if item.strip()],
    }
