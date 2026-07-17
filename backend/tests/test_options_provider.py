from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.marketdata.alpaca_options import AlpacaOptionsData, OptionsDataService
from app.marketdata.base import MarketDataError, UnknownSymbolError
from tests.fakes import Clock

OCC = "SPY260821C00625000"
SNAP = {
    "latestQuote": {"bp": 4.90, "ap": 5.10, "t": "2026-07-17T15:00:00Z"},
    "latestTrade": {"p": 5.05, "t": "2026-07-17T14:59:00Z"},
    "impliedVolatility": 0.172,
    "greeks": {"delta": 0.55, "gamma": 0.01, "theta": -0.12, "vega": 0.35, "rho": 0.05},
}


def provider_with(data_handler=None, contracts_handler=None, feed="indicative"):
    dt = httpx.MockTransport(data_handler) if data_handler else None
    ct = httpx.MockTransport(contracts_handler) if contracts_handler else None
    return AlpacaOptionsData("key", "secret", feed=feed,
                             data_transport=dt, contracts_transport=ct)


def test_quote_two_sided_is_mid_with_bid_ask():
    def handler(request):
        assert request.url.path == "/v1beta1/options/snapshots"
        assert request.url.params["symbols"] == OCC
        assert request.url.params["feed"] == "indicative"
        return httpx.Response(200, json={"snapshots": {OCC: SNAP}})

    q = provider_with(data_handler=handler).get_quote(OCC)
    assert q.bid == Decimal("4.9") and q.ask == Decimal("5.1")
    assert q.price == Decimal("5.0000")
    assert q.as_of.tzinfo is None


def test_quote_one_sided_falls_back_to_last():
    snap = {"latestQuote": {"bp": 0, "ap": 5.10}, "latestTrade": {"p": 5.05, "t": "2026-07-17T14:59:00Z"}}

    def handler(request):
        return httpx.Response(200, json={"snapshots": {OCC: snap}})

    q = provider_with(data_handler=handler).get_quote(OCC)
    assert q.bid is None and q.ask == Decimal("5.1")
    assert q.price == Decimal("5.05")


def test_quote_no_data_is_marketdataerror():
    def handler(request):
        return httpx.Response(200, json={"snapshots": {OCC: {"latestQuote": {"bp": 0, "ap": 0}}}})

    with pytest.raises(MarketDataError, match="no quote for contract"):
        provider_with(data_handler=handler).get_quote(OCC)


def test_quote_missing_contract_is_unknown_symbol():
    def handler(request):
        return httpx.Response(200, json={"snapshots": {}})

    with pytest.raises(UnknownSymbolError):
        provider_with(data_handler=handler).get_quote(OCC)


def test_422_is_marketdataerror_not_unknown_symbol():
    def handler(request):
        return httpx.Response(422, json={"message": "bad params"})

    with pytest.raises(MarketDataError) as exc:
        provider_with(data_handler=handler).get_quote(OCC)
    assert not isinstance(exc.value, UnknownSymbolError)


def test_404_is_unknown_symbol():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(UnknownSymbolError):
        provider_with(data_handler=handler).get_quote(OCC)


def test_get_bars_always_raises():
    with pytest.raises(MarketDataError, match="bars not available for option contracts"):
        provider_with().get_bars(OCC)


def test_500_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError) as exc:
        provider_with(data_handler=handler).get_quote(OCC)
    assert not isinstance(exc.value, UnknownSymbolError)


def test_contracts_sends_explicit_lte_and_paginates():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        assert request.url.path == "/v2/options/contracts"
        assert request.url.params["underlying_symbols"] == "SPY"
        assert request.url.params["limit"] == "10000"
        # Alpaca defaults lte to the upcoming weekend; we MUST override it.
        gte = date.fromisoformat(request.url.params["expiration_date_gte"])
        lte = date.fromisoformat(request.url.params["expiration_date_lte"])
        assert lte - gte >= timedelta(days=700)
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={
                "option_contracts": [
                    {"symbol": "SPY260821C00625000", "expiration_date": "2026-08-21",
                     "open_interest": "120"},
                ],
                "next_page_token": "tok2"})
        assert request.url.params["page_token"] == "tok2"
        return httpx.Response(200, json={
            "option_contracts": [
                {"symbol": "SPY260918C00630000", "expiration_date": "2026-09-18",
                 "open_interest": None},
            ],
            "next_page_token": None})

    rows = provider_with(contracts_handler=handler).get_contracts("SPY")
    assert len(rows) == 2 and len(calls) == 2


def test_chain_snapshots_paginates_and_sends_expiry():
    def handler(request):
        assert request.url.path == "/v1beta1/options/snapshots/SPY"
        assert request.url.params["expiration_date"] == "2026-08-21"
        assert request.url.params["feed"] == "indicative"
        assert request.url.params["limit"] == "1000"
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={"snapshots": {OCC: SNAP},
                                             "next_page_token": "n1"})
        return httpx.Response(200, json={
            "snapshots": {"SPY260821P00600000": SNAP}, "next_page_token": None})

    snaps = provider_with(data_handler=handler).get_chain_snapshots("SPY", date(2026, 8, 21))
    assert set(snaps) == {OCC, "SPY260821P00600000"}


class StubProvider:
    def __init__(self):
        self.quote_calls = 0
        self.contract_calls = 0
        self.chain_calls = 0

    def get_quote(self, symbol):
        self.quote_calls += 1
        from app.marketdata.base import Quote
        from app.timeutil import utcnow
        return Quote(symbol=symbol, price=Decimal("5"), as_of=utcnow(),
                     bid=Decimal("4.9"), ask=Decimal("5.1"))

    def get_bars(self, symbol, timeframe="1D", limit=200):
        raise MarketDataError("bars not available for option contracts")

    def get_contracts(self, underlying):
        self.contract_calls += 1
        return [
            {"symbol": "SPY260821C00625000", "expiration_date": "2026-08-21",
             "open_interest": "120"},
            {"symbol": "SPY260821P00600000", "expiration_date": "2026-08-21",
             "open_interest": "80"},
            {"symbol": "SPY260918C00630000", "expiration_date": "2026-09-18",
             "open_interest": None},
            {"symbol": "BADSYMBOL", "expiration_date": "2026-08-21",
             "open_interest": "5"},
        ]

    def get_chain_snapshots(self, underlying, expiry):
        self.chain_calls += 1
        return {  # 630 listed BEFORE 625 so the strike sort is exercised
            "SPY260821C00630000": {"latestQuote": {"bp": 3.0, "ap": 3.2}},
            "SPY260821C00625000": SNAP,
            "SPY260821P00600000": {"latestQuote": {"bp": 1.0, "ap": 1.2},
                                   "greeks": {"delta": -0.4}},
            "SPY7260821C00625000!": SNAP,  # adjusted/non-standard: filtered
        }


def test_service_expirations_sorted_distinct_and_cached():
    stub = StubProvider()
    clock = Clock(datetime(2026, 7, 17, 12, 0))
    svc = OptionsDataService(stub, now_fn=clock)
    assert svc.get_expirations("SPY") == [date(2026, 8, 21), date(2026, 9, 18)]
    svc.get_expirations("SPY")
    assert stub.contract_calls == 1  # 15-min cache
    clock.now += timedelta(seconds=901)
    svc.get_expirations("SPY")
    assert stub.contract_calls == 2


def test_service_expirations_empty_is_unknown_symbol():
    class Empty(StubProvider):
        def get_contracts(self, underlying):
            return []

    with pytest.raises(UnknownSymbolError):
        OptionsDataService(Empty()).get_expirations("XXXX")


def test_service_chain_merges_oi_filters_and_sorts():
    stub = StubProvider()
    svc = OptionsDataService(stub)
    calls, puts = svc.get_chain("SPY", date(2026, 8, 21))
    # strike-ascending despite the stub listing 630 first
    assert [r.symbol for r in calls] == ["SPY260821C00625000",
                                         "SPY260821C00630000"]
    assert calls[1].strike == Decimal("630") and calls[1].open_interest is None
    assert [r.symbol for r in puts] == ["SPY260821P00600000"]
    row = calls[0]
    assert row.strike == Decimal("625") and row.open_interest == Decimal("120")
    assert row.iv == Decimal("0.172") and row.theta == Decimal("-0.12")
    assert puts[0].delta == Decimal("-0.4") and puts[0].open_interest == Decimal("80")
    assert puts[0].last is None and puts[0].iv is None


def test_service_chain_and_quote_cached_30s():
    stub = StubProvider()
    clock = Clock(datetime(2026, 7, 17, 12, 0))
    svc = OptionsDataService(stub, now_fn=clock)
    svc.get_chain("SPY", date(2026, 8, 21))
    svc.get_chain("SPY", date(2026, 8, 21))
    assert stub.chain_calls == 1
    svc.get_quote(OCC)
    svc.get_quote(OCC)
    assert stub.quote_calls == 1
    clock.now += timedelta(seconds=31)
    svc.get_chain("SPY", date(2026, 8, 21))
    svc.get_quote(OCC)
    assert stub.chain_calls == 2 and stub.quote_calls == 2
