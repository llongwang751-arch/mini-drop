from fastapi import APIRouter, Depends, status

from ...db import Store
from ..dependencies import get_store
from ..schemas import ScheduleRequest

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("")
def list_schedules(store: Store = Depends(get_store)):
    return store.list_schedules()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleRequest, store: Store = Depends(get_store)):
    return store.create_schedule(**body.model_dump(exclude={"continuous"}))


@router.post("/run-due")
def run_due_schedules(store: Store = Depends(get_store)):
    return {"created": store.run_due_schedules()}


@router.post("/{schedule_id}/stop")
def stop_schedule(schedule_id: int, store: Store = Depends(get_store)):
    return store.stop_schedule(schedule_id)
