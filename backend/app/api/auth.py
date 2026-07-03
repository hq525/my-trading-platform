import hmac

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import SESSION_COOKIE, SESSION_MAX_AGE, get_deps, serializer
from app.api.schemas import LoginIn

router = APIRouter()


@router.post("/login")
def login(body: LoginIn, response: Response, deps=Depends(get_deps)):
    if not hmac.compare_digest(body.password, deps.settings.password):
        raise HTTPException(401, "wrong password")
    token = serializer(deps.settings.secret_key).dumps({"u": "owner"})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_MAX_AGE)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}
