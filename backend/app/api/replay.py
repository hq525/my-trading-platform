from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import (BarOut, CoverageOut, QuoteOut, ReplayAccountOut,
                             ReplaySessionCreateIn, ReplaySessionDetailOut,
                             ReplaySessionOut, StepResultOut)
from app.marketdata.base import UnknownSymbolError
from app.models import Account, ReplayBar, ReplaySession
from app.replay.market_data import ReplayMarketData
from app.replay.service import ReplayCreationError, create_session, delete_session
from app.replay.stepper import step_session

router = APIRouter(prefix="/replay", dependencies=[Depends(require_auth)])


def _session_or_404(session, session_id: int) -> ReplaySession:
    row = session.get(ReplaySession, session_id)
    if row is None:
        raise HTTPException(404, "no such replay session")
    return row


def _role(row: ReplaySession, account: Account) -> str:
    prefix = f"replay:{row.id}:strategy:"
    return (account.name[len(prefix):] if account.name.startswith(prefix)
            else "manual")


def _detail(session, row: ReplaySession) -> ReplaySessionDetailOut:
    accounts = session.scalars(select(Account).where(
        Account.replay_session_id == row.id).order_by(Account.id)).all()
    coverage = session.execute(
        select(ReplayBar.symbol, func.min(ReplayBar.date),
               func.max(ReplayBar.date))
        .where(ReplayBar.session_id == row.id)
        .group_by(ReplayBar.symbol).order_by(ReplayBar.symbol)).all()
    return ReplaySessionDetailOut(
        id=row.id, name=row.name, symbols=row.symbols,
        start_date=row.start_date, cursor_date=row.cursor_date,
        end_date=row.end_date, exhausted=row.exhausted,
        created_at=row.created_at,
        accounts=[ReplayAccountOut(id=a.id, name=a.name, role=_role(row, a))
                  for a in accounts],
        coverage=[CoverageOut(symbol=s, first_date=lo, last_date=hi)
                  for s, lo, hi in coverage])


@router.post("/sessions", response_model=ReplaySessionDetailOut, status_code=201)
def create(body: ReplaySessionCreateIn, session=Depends(get_session),
           deps=Depends(get_deps)):
    if deps.replay_sources is None:
        raise HTTPException(503, "replay sources not configured")
    try:
        row = create_session(
            session, deps.replay_sources, symbols=body.symbols,
            start_date=body.start_date, strategies=body.strategies,
            known_strategies=set(deps.runner.strategies),
            starting_cash=(body.starting_cash if body.starting_cash is not None
                           else deps.settings.starting_cash),
            name=body.name)
    except ReplayCreationError as e:
        raise HTTPException(400, str(e))
    return _detail(session, row)


@router.get("/sessions", response_model=list[ReplaySessionOut])
def list_sessions(session=Depends(get_session)):
    return session.scalars(
        select(ReplaySession).order_by(ReplaySession.id.desc())).all()


@router.get("/sessions/{session_id}", response_model=ReplaySessionDetailOut)
def session_detail(session_id: int, session=Depends(get_session)):
    return _detail(session, _session_or_404(session, session_id))


@router.post("/sessions/{session_id}/step", response_model=StepResultOut)
def step(session_id: int, steps: int = Query(1, ge=1, le=250),
         session=Depends(get_session), deps=Depends(get_deps)):
    _session_or_404(session, session_id)
    try:
        result = step_session(session, deps, session_id, steps=steps)
    except ValueError:
        raise HTTPException(404, "no such replay session")
    return StepResultOut(
        cursor_date=result.cursor_date, fills=result.fills,
        expired=result.expired,
        cancelled_at_exhaustion=result.cancelled_at_exhaustion,
        strategy_errors=result.strategy_errors, exhausted=result.exhausted)


@router.delete("/sessions/{session_id}")
def delete(session_id: int, session=Depends(get_session)):
    _session_or_404(session, session_id)
    try:
        delete_session(session, session_id)
    except ValueError:
        raise HTTPException(404, "no such replay session")
    return {"ok": True}


@router.get("/sessions/{session_id}/bars/{symbol}",
            response_model=list[BarOut])
def bars(session_id: int, symbol: str, limit: int = Query(520, ge=1, le=1000),
         session=Depends(get_session)):
    row = _session_or_404(session, session_id)
    try:
        return ReplayMarketData(session, row, strict=False).get_bars(
            symbol.upper(), "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, "symbol not in this session")


@router.get("/sessions/{session_id}/quote/{symbol}", response_model=QuoteOut)
def quote(session_id: int, symbol: str, session=Depends(get_session)):
    row = _session_or_404(session, session_id)
    try:
        q = ReplayMarketData(session, row, strict=False).get_quote(symbol.upper())
    except UnknownSymbolError:
        raise HTTPException(404, "symbol not in this session")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of)
