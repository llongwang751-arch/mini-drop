import argparse
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routers import (
    agents_router,
    audit_router,
    continuous_router,
    health_router,
    natural_language_router,
    tasks_router,
)
from .db import Store


WEB = Path(__file__).with_name("web")


def create_app(database_url: str | None = None, start_watcher: bool = True) -> FastAPI:
    store = Store(database_url or os.getenv("DATABASE_URL", "sqlite:///data/minidrop.db"))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_watcher:
            threading.Thread(target=_offline_loop, args=(store,), daemon=True).start()
        yield

    app = FastAPI(title="Mini-Drop API", version="1.0.0", lifespan=lifespan)
    app.state.store = store

    _include_api_routers(app)
    _register_exception_handlers(app)
    _mount_frontend(app)
    return app


def _include_api_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(agents_router)
    app.include_router(tasks_router)
    app.include_router(audit_router)
    app.include_router(continuous_router)
    app.include_router(natural_language_router)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_request, exc):
        return JSONResponse(status_code=404, content={"error": str(exc)})


def _mount_frontend(app: FastAPI) -> None:
    if not WEB.exists():
        return

    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        target = WEB / path
        return FileResponse(target if target.is_file() else WEB / "index.html")


def _offline_loop(store: Store) -> None:
    while True:
        store.mark_offline()
        time.sleep(5)


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=None, help="SQLAlchemy database URL; defaults to DATABASE_URL")
    args = parser.parse_args()
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
