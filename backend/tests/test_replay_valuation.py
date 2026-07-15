from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Position
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.live_fixtures import make_live_deps


def test_account_detail_values_replay_positions_from_session_bars(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    app = create_app(deps, start_scheduler=False)
    client = TestClient(app)
    client.post("/api/login", json={"password": "pw"})

    with session_factory() as db:
        row = make_replay_session(db, symbols=("SPY",), start="2024-06-03",
                                  cursor="2024-06-04", end="2024-06-05")
        make_replay_bar(db, row.id, "SPY", "2024-06-03", close="100")
        make_replay_bar(db, row.id, "SPY", "2024-06-04", close="120")
        acct = make_replay_account(db, row.id, cash="99880")
        db.add(Position(account_id=acct.id, symbol="SPY", qty=Decimal("1"),
                        avg_cost=Decimal("120"), realized_pnl=Decimal("0")))
        db.commit()
        acct_id = acct.id

    # The paper stack's fake quote for SPY is "100" (live world); the replay
    # branch must value at the session bar close 120 instead.
    detail = client.get(f"/api/accounts/{acct_id}").json()
    assert detail["positions"][0]["last_price"] == "120"
    assert detail["equity"] == "100000"  # 99880 cash + 120 market value


def test_account_detail_replay_branch_survives_dead_symbols(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    app = create_app(deps, start_scheduler=False)
    client = TestClient(app)
    client.post("/api/login", json={"password": "pw"})

    with session_factory() as db:
        row = make_replay_session(db, symbols=("XYZ",), start="2024-06-03",
                                  cursor="2024-06-05", end="2024-06-05")
        make_replay_bar(db, row.id, "XYZ", "2024-06-03", close="50")
        make_replay_bar(db, row.id, "XYZ", "2024-06-05", close="55")
        acct = make_replay_account(db, row.id)
        db.add(Position(account_id=acct.id, symbol="XYZ", qty=Decimal("2"),
                        avg_cost=Decimal("50"), realized_pnl=Decimal("0")))
        db.commit()
        # simulate mid-session coverage end: delete the 06-05 bar
        from sqlalchemy import delete

        from app.models import ReplayBar
        db.execute(delete(ReplayBar).where(ReplayBar.date == row.end_date))
        db.commit()
        acct_id = acct.id

    detail = client.get(f"/api/accounts/{acct_id}").json()
    assert detail["positions"][0]["last_price"] == "50"  # last available close
