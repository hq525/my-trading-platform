from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import AccountDetailOut, AccountOut, PositionOut, SnapshotOut
from app.engine.valuation import account_equity, position_values
from app.marketdata.base import MarketDataError
from app.models import Account, EquitySnapshot, ReplaySession
from app.replay.market_data import ReplayMarketData

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
    if account.mode == "replay":
        session_row = session.get(ReplaySession, account.replay_session_id)
        replay_md = ReplayMarketData(session, session_row, strict=False)
        lookup = lambda symbol: replay_md  # noqa: E731
    else:
        lookup = deps.market_data_for_symbol
    try:
        values = position_values(session, account, lookup)
        equity = account_equity(session, account, lookup)
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return AccountDetailOut(
        id=account.id, name=account.name, kind=account.kind, mode=account.mode,
        cash=account.cash, starting_cash=account.starting_cash,
        last_synced_at=account.last_synced_at, sync_detail=account.sync_detail,
        equity=equity,
        positions=[PositionOut(**vars(pv)) for pv in values])


@router.get("/accounts/{account_id}/snapshots", response_model=list[SnapshotOut])
def snapshots(account_id: int, session=Depends(get_session)):
    _account_or_404(session, account_id)
    rows = session.scalars(select(EquitySnapshot)
                           .where(EquitySnapshot.account_id == account_id)
                           .order_by(EquitySnapshot.date)).all()
    return [SnapshotOut(date=str(r.date), equity=r.equity, cash=r.cash)
            for r in rows]
