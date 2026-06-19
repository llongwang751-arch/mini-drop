from fastapi import APIRouter, Depends

from ...db import Store
from ..dependencies import get_store

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(store: Store = Depends(get_store)):
    return store.list_audit()
