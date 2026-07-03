from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_session, require_auth
from app.api.schemas import StatsOut, TradeOut
from app.models import Fill, JournalNote, Order

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/journal", response_model=list[TradeOut])
def journal(account_id: int, session=Depends(get_session)):
    fills = session.scalars(
        select(Fill).join(Order).where(Order.account_id == account_id)
        .order_by(Fill.filled_at.desc(), Fill.id.desc())).all()
    notes = {n.order_id: n.text for n in session.scalars(select(JournalNote))}
    return [TradeOut(order_id=f.order_id, symbol=f.order.symbol,
                     side=f.order.side, qty=f.qty, price=f.price,
                     commission=f.commission, realized_pnl=f.realized_pnl,
                     filled_at=f.filled_at, note=notes.get(f.order_id))
            for f in fills]


@router.get("/journal/stats", response_model=StatsOut)
def stats(account_id: int, session=Depends(get_session)):
    realized = [f.realized_pnl for f in session.scalars(
        select(Fill).join(Order).where(
            Order.account_id == account_id,
            Fill.realized_pnl.is_not(None))).all()]
    if not realized:
        return StatsOut(closed_trades=0, wins=0, win_rate=None,
                        avg_gain=None, avg_loss=None)
    gains = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]
    return StatsOut(
        closed_trades=len(realized),
        wins=len(gains),
        win_rate=len(gains) / len(realized),
        avg_gain=(sum(gains, Decimal("0")) / len(gains)).quantize(Decimal("0.01"))
        if gains else None,
        avg_loss=(sum(losses, Decimal("0")) / len(losses)).quantize(Decimal("0.01"))
        if losses else None)
