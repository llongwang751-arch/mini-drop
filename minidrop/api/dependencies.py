from fastapi import Request

from ..db import Store


def get_store(request: Request) -> Store:
    return request.app.state.store
