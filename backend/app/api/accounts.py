from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import AccountDetailOut, AccountOut, PositionOut, SnapshotOut
from app.engine.valuation import account_equity, position_values
from app.marketdata.base import MarketDataError
from app.models import Account, EquitySnapshot

router = APIRouter(dependencies=[Depends(require_auth)])


def _account_or_404(session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "no such account")
    return account


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session=Depends(get_session)):
    return session.scalars(select(Account)).all()


@router.get("/accounts/{account_id}", response_model=AccountDetailOut)
def account_detail(account_id: int, session=Depends(get_session),
                   deps=Depends(get_deps)):
    account = _account_or_404(session, account_id)
    try:
        values = position_values(session, account, deps.market_data_for_symbol)
        equity = account_equity(session, account, deps.market_data_for_symbol)
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return AccountDetailOut(
        id=account.id, name=account.name, kind=account.kind,
        cash=account.cash, starting_cash=account.starting_cash, equity=equity,
        positions=[PositionOut(**vars(pv)) for pv in values])


@router.get("/accounts/{account_id}/snapshots", response_model=list[SnapshotOut])
def snapshots(account_id: int, session=Depends(get_session)):
    _account_or_404(session, account_id)
    rows = session.scalars(select(EquitySnapshot)
                           .where(EquitySnapshot.account_id == account_id)
                           .order_by(EquitySnapshot.date)).all()
    return [SnapshotOut(date=str(r.date), equity=r.equity, cash=r.cash)
            for r in rows]
