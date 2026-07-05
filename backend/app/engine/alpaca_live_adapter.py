from __future__ import annotations

import httpx
from sqlalchemy import select

from app.assets import is_crypto_symbol
from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Account, Order
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
        order.broker_order_id = r.json()["id"]
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

    @staticmethod
    def _error_message(r: httpx.Response) -> str:
        try:
            return r.json().get("message") or f"HTTP {r.status_code}"
        except ValueError:
            return f"HTTP {r.status_code}"
