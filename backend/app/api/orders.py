from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import NoteIn, OrderIn, OrderOut
from app.engine.alpaca_live_adapter import BrokerError
from app.engine.engine import InvalidOrderState
from app.models import Account, JournalNote, Order

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/accounts/{account_id}/orders", response_model=OrderOut,
             status_code=201)
def place_order(account_id: int, body: OrderIn, session=Depends(get_session),
                deps=Depends(get_deps)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "no such account")
    execution = deps.execution_for(account, body.symbol)
    if execution is None:
        # Live account exists but trading keys were removed from the env.
        raise HTTPException(503, "live trading not configured"
                            if account.mode == "live"
                            else "options trading not configured")
    return execution.place_order(
        session, account_id=account_id, symbol=body.symbol, side=body.side,
        order_type=body.order_type, qty=body.qty, tif=body.tif,
        limit_price=body.limit_price, idempotency_key=body.idempotency_key)


@router.get("/accounts/{account_id}/orders", response_model=list[OrderOut])
def list_orders(account_id: int, status: str | None = None,
                session=Depends(get_session)):
    stmt = (select(Order).where(Order.account_id == account_id)
            .order_by(Order.placed_at.desc()))
    if status is not None:
        stmt = stmt.where(Order.status == status)
    return session.scalars(stmt).all()


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, session=Depends(get_session),
                 deps=Depends(get_deps)):
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "no such order")
    execution = deps.execution_for(order.account, order.symbol)
    if execution is None:
        raise HTTPException(503, "live trading not configured"
                            if order.account.mode == "live"
                            else "options trading not configured")
    try:
        return execution.cancel_order(session, order_id)
    except ValueError:
        raise HTTPException(404, "no such order")
    except InvalidOrderState as e:
        raise HTTPException(409, str(e))
    except BrokerError as e:
        raise HTTPException(502, str(e))


@router.put("/orders/{order_id}/note")
def upsert_note(order_id: int, body: NoteIn, session=Depends(get_session)):
    if session.get(Order, order_id) is None:
        raise HTTPException(404, "no such order")
    note = session.scalar(select(JournalNote).where(
        JournalNote.order_id == order_id))
    if note is None:
        session.add(JournalNote(order_id=order_id, text=body.text))
    else:
        note.text = body.text
    return {"ok": True}
