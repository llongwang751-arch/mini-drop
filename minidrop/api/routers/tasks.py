from fastapi import APIRouter, Depends, HTTPException, status

from ...analyzer import analyze
from ...db import Store
from ..dependencies import get_store
from ..schemas import FailurePayload, TaskRequest, UploadPayload

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(store: Store = Depends(get_store)):
    return store.list_tasks()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(body: TaskRequest, store: Store = Depends(get_store)):
    return store.create_task(**body.model_dump())


@router.get("/{task_id}")
def get_task(task_id: str, store: Store = Depends(get_store)):
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/{task_id}/upload")
def upload_task(task_id: str, body: UploadPayload, store: Store = Depends(get_store)):
    store.transition(task_id, "UPLOADING", body.reason)
    store.set_payload(task_id, "raw_data", body.raw)
    store.set_payload(task_id, "result", analyze(body.raw))
    completed = store.transition(task_id, "DONE", "analyzer produced visualizations")
    if completed["continuous"]:
        store.create_task(
            completed["agent_id"],
            completed["pid"],
            completed["duration"],
            completed["rate"],
            completed["collector"],
            True,
        )
    return completed


@router.post("/{task_id}/stop-continuous")
def stop_continuous(task_id: str, store: Store = Depends(get_store)):
    return store.stop_continuous(task_id)


@router.post("/{task_id}/fail")
def fail_task(task_id: str, body: FailurePayload, store: Store = Depends(get_store)):
    return store.transition(task_id, "FAILED", body.reason)
