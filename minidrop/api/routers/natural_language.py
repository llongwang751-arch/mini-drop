from fastapi import APIRouter, Depends, status

from ...db import Store
from ...planner import plan_collection
from ..dependencies import get_store
from ..schemas import NaturalLanguageRequest

router = APIRouter(prefix="/api/natural-language", tags=["natural-language"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_from_natural_language(body: NaturalLanguageRequest, store: Store = Depends(get_store)):
    plan = plan_collection(body.text, store.list_agents())
    return {"plan": plan, "task": store.create_task(**plan)}
