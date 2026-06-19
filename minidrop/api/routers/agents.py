from fastapi import APIRouter, Depends

from ...db import Store
from ..dependencies import get_store

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(store: Store = Depends(get_store)):
    store.mark_offline()
    return store.list_agents()


@router.post("/{agent_id}/heartbeat")
def heartbeat(agent_id: str, body: dict, store: Store = Depends(get_store)):
    store.heartbeat(agent_id, body.get("hostname", "unknown"), body.get("version", "unknown"), body)
    return {"ok": True}


@router.post("/{agent_id}/claim")
def claim(agent_id: str, store: Store = Depends(get_store)):
    return store.claim_task(agent_id)
