from decimal import Decimal

from app.strategy.base import Strategy


class SmaCross(Strategy):
    """Hold SPY while its 20-day SMA is above its 50-day SMA."""

    schedule = "daily_after_close"

    def run(self, ctx):
        bars = ctx.get_bars("SPY", "1D", 60)
        closes = [b.close for b in bars]
        if len(closes) < 50:
            return
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        held = {p.symbol: p for p in ctx.positions()}.get("SPY")
        if sma20 > sma50 and held is None:
            price = ctx.get_quote("SPY").price
            qty = int((ctx.cash * Decimal("0.95")) / price)
            if qty > 0:
                ctx.buy("SPY", qty)  # market order after close -> fills at next open
        elif sma20 < sma50 and held is not None:
            ctx.sell("SPY", held.qty)
