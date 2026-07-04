from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_deps, require_auth
from app.api.schemas import BarOut, QuoteOut
from app.marketdata.base import MarketDataError, UnknownSymbolError

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/market/quote/{symbol}", response_model=QuoteOut)
def quote(symbol: str, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        q = deps.market_data_for_symbol(symbol).get_quote(symbol)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of)


@router.get("/market/bars/{symbol}", response_model=list[BarOut])
def bars(symbol: str, limit: int = 200, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        return deps.market_data_for_symbol(symbol).get_bars(symbol, "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
