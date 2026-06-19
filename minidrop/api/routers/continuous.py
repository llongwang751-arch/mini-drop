from fastapi import APIRouter, Depends, Query

from ...db import Store
from ..dependencies import get_store

router = APIRouter(prefix="/api/continuous", tags=["continuous-profiling"])


@router.get("/{agent_id}")
def continuous_window(
    agent_id: str,
    start: float | None = Query(None),
    end: float | None = Query(None),
    store: Store = Depends(get_store),
):
    return store.continuous_window(agent_id, start, end)
