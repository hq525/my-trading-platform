"""Single source of truth for asset-class routing.

Classification order everywhere: option -> crypto -> stock.
- Option: compact OCC symbol (ROOT + YYMMDD + C/P + strike*1000 zero-padded
  to 8 digits, e.g. "SPY260821C00625000"). No dash, so it can never collide
  with the crypto heuristic.
- Crypto: "-" in symbol (e.g. "BTC-USD"); stock tickers never contain "-".
- Stock: everything else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

_OCC_RE = re.compile(r"^[A-Z]{1,6}(\d{6})[CP]\d{8}$")


def is_crypto_symbol(symbol: str) -> bool:
    return "-" in symbol


def is_option_symbol(symbol: str) -> bool:
    m = _OCC_RE.match(symbol)
    if m is None:
        return False
    try:
        datetime.strptime(m.group(1), "%y%m%d")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class OccContract:
    underlying: str
    expiry: date
    right: str  # "call" | "put"
    strike: Decimal


def parse_occ(symbol: str) -> OccContract:
    if not is_option_symbol(symbol):
        raise ValueError(f"not an OCC option symbol: {symbol}")
    root_len = len(symbol) - 15
    expiry = datetime.strptime(symbol[root_len:root_len + 6], "%y%m%d").date()
    right = "call" if symbol[root_len + 6] == "C" else "put"
    strike = Decimal(symbol[root_len + 7:]) / Decimal("1000")
    return OccContract(underlying=symbol[:root_len], expiry=expiry,
                       right=right, strike=strike)


def contract_multiplier(symbol: str) -> Decimal:
    """100 for option contracts, 1 for everything else. The ONLY source of
    the x100 — engine, valuation, and adapters must use this, never a
    literal."""
    return Decimal("100") if is_option_symbol(symbol) else Decimal("1")
