from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "pt_session"
SESSION_MAX_AGE = 30 * 86400


def serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="pt-session")


def get_deps(request: Request):
    return request.app.state.deps


def get_session(deps=Depends(get_deps)):
    with deps.session_factory() as session:
        yield session
        session.commit()


def require_auth(request: Request, deps=Depends(get_deps)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        serializer(deps.settings.secret_key).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "invalid session")
