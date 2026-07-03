from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import RunOut, StrategyOut
from app.models import Account, StrategyRun, StrategyState

router = APIRouter(dependencies=[Depends(require_auth)])


def _strategy_out(session, deps, name: str) -> StrategyOut:
    cls = deps.runner.strategies[name]
    state = session.scalar(select(StrategyState).where(StrategyState.name == name))
    account = session.scalar(select(Account).where(
        Account.name == f"strategy:{name}"))
    return StrategyOut(name=name, schedule=cls.schedule,
                       enabled=bool(state and state.enabled),
                       account_id=account.id)


@router.get("/strategies", response_model=list[StrategyOut])
def list_strategies(session=Depends(get_session), deps=Depends(get_deps)):
    return [_strategy_out(session, deps, name)
            for name in sorted(deps.runner.strategies)]


@router.post("/strategies/{name}/toggle", response_model=StrategyOut)
def toggle(name: str, session=Depends(get_session), deps=Depends(get_deps)):
    if name not in deps.runner.strategies:
        raise HTTPException(404, f"no such strategy: {name}")
    state = session.scalar(select(StrategyState).where(StrategyState.name == name))
    state.enabled = not state.enabled
    session.flush()
    return _strategy_out(session, deps, name)


@router.get("/strategies/{name}/runs", response_model=list[RunOut])
def runs(name: str, limit: int = 20, session=Depends(get_session),
         deps=Depends(get_deps)):
    if name not in deps.runner.strategies:
        raise HTTPException(404, f"no such strategy: {name}")
    return session.scalars(
        select(StrategyRun).where(StrategyRun.strategy_name == name)
        .order_by(StrategyRun.started_at.desc()).limit(limit)).all()
