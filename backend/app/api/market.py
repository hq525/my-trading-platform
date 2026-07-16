from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_deps, require_auth
from app.api.schemas import (BarOut, OptionChainOut, OptionExpirationsOut,
                             QuoteOut)
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
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of,
                    bid=q.bid, ask=q.ask)


@router.get("/market/bars/{symbol}", response_model=list[BarOut])
def bars(symbol: str, limit: int = 200, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        return deps.market_data_for_symbol(symbol).get_bars(symbol, "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")


@router.get("/market/options/{underlying}/expirations",
            response_model=OptionExpirationsOut)
def option_expirations(underlying: str, deps=Depends(get_deps)):
    underlying = underlying.upper()
    if deps.options_market_data is None:
        raise HTTPException(503, "options data not configured")
    try:
        dates = deps.options_market_data.get_expirations(underlying)
    except UnknownSymbolError:
        raise HTTPException(404, "no options listed for symbol")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return OptionExpirationsOut(underlying=underlying, expirations=dates)


@router.get("/market/options/{underlying}/chain", response_model=OptionChainOut)
def option_chain(underlying: str, expiry: date, deps=Depends(get_deps)):
    underlying = underlying.upper()
    if deps.options_market_data is None:
        raise HTTPException(503, "options data not configured")
    try:
        calls, puts = deps.options_market_data.get_chain(underlying, expiry)
    except UnknownSymbolError:
        raise HTTPException(404, "no options listed for symbol")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return OptionChainOut(underlying=underlying, expiry=expiry,
                          calls=calls, puts=puts)
