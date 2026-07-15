import sqlite3
from datetime import date
from decimal import Decimal

from app.db import init_db, make_engine
from tests.factories import make_account, make_replay_account, make_replay_bar, make_replay_session


def test_replay_session_properties(session):
    row = make_replay_session(session, symbols=("SPY", "BTC-USD"),
                              strategies=("SmaCross",),
                              start="2024-06-03", end="2024-06-28")
    assert row.symbols == ["SPY", "BTC-USD"]
    assert row.strategies == ["SmaCross"]
    assert row.exhausted is False
    row.cursor_date = date(2024, 6, 28)
    assert row.exhausted is True


def test_replay_bar_and_account_link(session):
    row = make_replay_session(session)
    bar = make_replay_bar(session, row.id, "SPY", "2024-06-03",
                          open_="100", high="102", low="99", close="101")
    acct = make_replay_account(session, row.id)
    assert bar.close == Decimal("101")
    assert acct.mode == "replay"
    assert acct.replay_session_id == row.id
    assert acct.name == f"replay:{row.id}:manual"


def test_regular_accounts_have_no_replay_session(session):
    acct = make_account(session)
    assert acct.replay_session_id is None


def test_init_db_adds_replay_session_id_to_existing_database(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, "
        "kind VARCHAR, cash VARCHAR, starting_cash VARCHAR, commission VARCHAR, "
        "created_at DATETIME, mode VARCHAR DEFAULT 'paper', "
        "last_synced_at DATETIME, sync_detail VARCHAR)")
    conn.commit()
    conn.close()
    engine = make_engine(f"sqlite:///{db}")
    init_db(engine)
    with engine.connect() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(accounts)")}
        assert "replay_session_id" in cols
        tables = {r[0] for r in c.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"replay_sessions", "replay_bars"} <= tables
