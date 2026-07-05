from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy import select

from app.assets import is_crypto_symbol
from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Account, Order, Position
from app.timeutil import utcnow


class BrokerError(Exception):
    """The broker API could not be reached or gave an unusable answer."""


class AlpacaLiveAdapter:
    """Live execution via Alpaca's brokerage API (paper endpoint by default).

    Alpaca decides fills; this adapter mirrors them into the local ledger.
    Local engine validation and cash reservation still run first so the
    books stay balanced by construction, and a periodic sync overwrites
    local cash with Alpaca's figure (Alpaca is the source of truth).
    """

    def __init__(self, engine: TradingEngine, base_url: str, key_id: str,
                 secret: str, transport: httpx.BaseTransport | None = None,
                 now_fn=utcnow):
        self.engine = engine
        self.now_fn = now_fn
        self._client = httpx.Client(
            base_url=base_url,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
            timeout=10,
            transport=transport,
        )

    def place_order(self, session, **kwargs) -> Order:
        order = self.engine.place_order(session, **kwargs)
        if order.status != "pending":
            return order
        if is_crypto_symbol(order.symbol):
            return self.engine.reject_order(
                session, order, "crypto not supported in live trading yet")
        body = {"symbol": order.symbol, "qty": str(order.qty),
                "side": order.side, "type": order.order_type,
                "time_in_force": order.tif,
                "client_order_id": str(order.id)}
        if order.order_type == "limit":
            body["limit_price"] = str(order.limit_price)
        try:
            r = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as e:
            return self.engine.reject_order(
                session, order, f"broker unreachable: {e}")
        if r.status_code not in (200, 201):
            return self.engine.reject_order(
                session, order, f"broker rejected: {self._error_message(r)}")
        try:
            order.broker_order_id = r.json()["id"]
        except (ValueError, KeyError, TypeError):
            return self.engine.reject_order(
                session, order, "broker rejected: malformed response")
        return order

    def cancel_order(self, session, order_id: int) -> Order:
        order = session.get(Order, order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        if order.status != "pending":
            raise InvalidOrderState(
                f"cannot cancel order in status {order.status}")
        if order.broker_order_id is None:
            # Defensive: a pending order that never reached the broker.
            return self.engine.cancel_order(session, order_id)
        try:
            self._client.delete(f"/v2/orders/{order.broker_order_id}")
        except httpx.HTTPError as e:
            raise BrokerError(f"broker unreachable: {e}") from e
        # Regardless of the DELETE response (204 accepted, 422 already
        # terminal, 404 unknown): a cancel can race a fill, so the next
        # poll mirrors Alpaca's final state instead of guessing here.
        return order

    def process_pending(self, session, now=None) -> None:
        pending = session.scalars(
            select(Order).join(Account).where(
                Order.status == "pending", Account.mode == "live")).all()
        for order in pending:
            if order.broker_order_id is None:
                continue  # never reached the broker; nothing to mirror
            try:
                r = self._client.get(f"/v2/orders/{order.broker_order_id}")
            except httpx.HTTPError:
                continue  # wait for the next cycle
            if r.status_code != 200:
                continue
            try:
                data = r.json()
                status = data["status"]
            except (ValueError, KeyError, TypeError):
                continue  # malformed body; try again next cycle
            if status == "filled":
                try:
                    filled_avg_price = Decimal(data["filled_avg_price"])
                except (ValueError, KeyError, TypeError, ArithmeticError):
                    continue  # malformed body; try again next cycle
                self.engine.apply_fill(session, order, filled_avg_price)
            elif status == "canceled":
                self.engine.cancel_order(session, order.id)
            elif status == "expired":
                self.engine.expire_order(session, order)
            elif status == "rejected":
                reason = data.get("reason") or "unspecified"
                self.engine.reject_order(session, order,
                                         f"broker rejected: {reason}")
            # anything else (new, accepted, partially_filled, ...) waits

    def sync_account(self, session) -> None:
        account = session.scalar(select(Account).where(Account.mode == "live"))
        if account is None:
            return
        try:
            acct_r = self._client.get("/v2/account")
            pos_r = self._client.get("/v2/positions")
        except httpx.HTTPError:
            return  # keep last-known values; last_synced_at ages visibly
        if acct_r.status_code != 200 or pos_r.status_code != 200:
            return
        account.cash = Decimal(acct_r.json()["cash"])
        remote = {p["symbol"]: Decimal(p["qty"]) for p in pos_r.json()}
        local_rows = session.scalars(select(Position).where(
            Position.account_id == account.id)).all()
        # qty is TEXT in SQLite: compare in Python, never in SQL.
        local = {p.symbol: p.qty for p in local_rows if p.qty > 0}
        diffs = [f"{s}: local {local.get(s, Decimal('0'))}, "
                 f"alpaca {remote.get(s, Decimal('0'))}"
                 for s in sorted(set(local) | set(remote))
                 if local.get(s, Decimal("0")) != remote.get(s, Decimal("0"))]
        account.sync_detail = "; ".join(diffs) if diffs else None
        account.last_synced_at = self.now_fn()

    @staticmethod
    def _error_message(r: httpx.Response) -> str:
        try:
            return r.json().get("message") or f"HTTP {r.status_code}"
        except ValueError:
            return f"HTTP {r.status_code}"
