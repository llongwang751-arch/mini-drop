from fastapi import APIRouter, Depends

from ..dependencies import get_current_user

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/auth/check")
def auth_check(user: dict = Depends(get_current_user)):
    return {"ok": True, "user": user}


@router.get("/users/me")
def current_user(user: dict = Depends(get_current_user)):
    return user
