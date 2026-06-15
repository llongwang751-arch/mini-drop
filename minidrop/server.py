import argparse
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analyzer import analyze
from .db import Store
from .planner import plan_collection


LOG = logging.getLogger("minidrop.server")
WEB = Path(__file__).with_name("web")


class TaskRequest(BaseModel):
    agent_id: str
    pid: int = Field(gt=0)
    duration: int = Field(default=5, ge=1, le=300)
    rate: int = Field(default=49, ge=1, le=999)
    collector: str
    continuous: bool = False


class NaturalLanguageRequest(BaseModel):
    text: str


class Payload(BaseModel):
    raw: dict
    reason: str = "agent uploaded raw profile"


class Failure(BaseModel):
    reason: str = "unspecified agent failure"


def create_app(database_url=None, start_watcher=True):
    store = Store(database_url or os.getenv("DATABASE_URL", "sqlite:///data/minidrop.db"))

    @asynccontextmanager
    async def lifespan(_app):
        if start_watcher:
            threading.Thread(target=_offline_loop, args=(store,), daemon=True).start()
        yield

    app = FastAPI(title="Mini-Drop API", version="1.0.0", lifespan=lifespan)
    app.state.store = store

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/agents")
    def agents():
        store.mark_offline()
        return store.list_agents()

    @app.get("/api/tasks")
    def tasks():
        return store.list_tasks()

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        value = store.get_task(task_id)
        if not value:
            raise HTTPException(404, "task not found")
        return value

    @app.get("/api/audit")
    def audit():
        return store.list_audit()

    @app.get("/api/continuous/{agent_id}")
    def continuous(agent_id: str, start: float | None = Query(None), end: float | None = Query(None)):
        return store.continuous_window(agent_id, start, end)

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskRequest):
        return store.create_task(**body.model_dump())

    @app.post("/api/natural-language", status_code=201)
    def natural_language(body: NaturalLanguageRequest):
        plan = plan_collection(body.text, store.list_agents())
        return {"plan": plan, "task": store.create_task(**plan)}

    @app.post("/api/agents/{agent_id}/heartbeat")
    def heartbeat(agent_id: str, body: dict):
        store.heartbeat(agent_id, body.get("hostname", "unknown"), body.get("version", "unknown"), body)
        return {"ok": True}

    @app.post("/api/agents/{agent_id}/claim")
    def claim(agent_id: str):
        return store.claim_task(agent_id)

    @app.post("/api/tasks/{task_id}/upload")
    def upload(task_id: str, body: Payload):
        store.transition(task_id, "UPLOADING", body.reason)
        store.set_payload(task_id, "raw_data", body.raw)
        store.set_payload(task_id, "result", analyze(body.raw))
        completed = store.transition(task_id, "DONE", "analyzer produced visualizations")
        if completed["continuous"]:
            store.create_task(completed["agent_id"], completed["pid"], completed["duration"],
                              completed["rate"], completed["collector"], True)
        return completed

    @app.post("/api/tasks/{task_id}/stop-continuous")
    def stop_continuous(task_id: str):
        return store.stop_continuous(task_id)

    @app.post("/api/tasks/{task_id}/fail")
    def fail(task_id: str, body: Failure):
        return store.transition(task_id, "FAILED", body.reason)

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_request, exc):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    if WEB.exists():
        app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            target = WEB / path
            return FileResponse(target if target.is_file() else WEB / "index.html")
    return app


def _offline_loop(store):
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
