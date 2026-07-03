from fastapi import APIRouter, Depends

from app.api.deps import get_session, require_auth
from app.models import Account
from sqlalchemy import select

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/accounts")
def list_accounts(session=Depends(get_session)):
    return [{"id": a.id, "name": a.name} for a in session.scalars(select(Account))]
