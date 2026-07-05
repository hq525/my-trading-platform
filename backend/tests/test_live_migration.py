import sqlite3

from app.config import Settings
from app.db import init_db, make_engine
from app.models import Account, Order
from tests.factories import make_account


def test_trading_settings_default_to_disabled():
    s = Settings(_env_file=None)
    assert s.alpaca_trading_key_id == ""
    assert s.alpaca_trading_secret == ""
    assert s.alpaca_trading_base == "https://paper-api.alpaca.markets"


def test_new_accounts_default_to_paper_mode(session):
    acct = make_account(session)
    assert acct.mode == "paper"
    assert acct.last_synced_at is None
    assert acct.sync_detail is None


def test_new_orders_have_no_broker_order_id(session):
    acct = make_account(session)
    order = Order(account_id=acct.id, symbol="SPY", side="buy",
                  order_type="market", qty=1)
    session.add(order)
    session.flush()
    assert order.broker_order_id is None


def test_init_db_adds_live_columns_to_existing_database(tmp_path):
    # A database created before Phase 3 lacks the new columns; init_db
    # (create_all skips existing tables) must ALTER them in.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, "
        "kind VARCHAR, cash VARCHAR, starting_cash VARCHAR, commission VARCHAR, "
        "created_at DATETIME)")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id INTEGER, "
        "symbol VARCHAR, side VARCHAR, order_type VARCHAR, tif VARCHAR, "
        "qty VARCHAR, limit_price VARCHAR, status VARCHAR, reject_reason VARCHAR, "
        "reserved_cash VARCHAR, idempotency_key VARCHAR, placed_at DATETIME)")
    conn.execute("INSERT INTO accounts (name, kind, cash, starting_cash, commission) "
                 "VALUES ('manual', 'manual', '100000', '100000', '0')")
    conn.commit()
    conn.close()

    engine = make_engine(f"sqlite:///{db}")
    init_db(engine)

    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT mode, last_synced_at, sync_detail FROM accounts").fetchone()
        assert row == ("paper", None, None)
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(orders)")}
        assert "broker_order_id" in cols
