from decimal import Decimal

from sqlalchemy import select

from app.main import create_app
from app.models import Account
from tests.live_fixtures import default_live_handler, make_live_deps


def test_startup_creates_and_syncs_live_account(session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path, default_live_handler)
    create_app(deps, start_scheduler=False)
    with session_factory() as s:
        live = s.scalar(select(Account).where(Account.mode == "live"))
        assert live is not None
        assert live.name == "live"
        assert live.cash == Decimal("50000")  # synced, not the placeholder 0
        assert live.last_synced_at is not None


def test_startup_without_live_adapter_creates_no_live_account(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path, live_handler=None)
    create_app(deps, start_scheduler=False)
    with session_factory() as s:
        assert s.scalar(select(Account).where(Account.mode == "live")) is None


def test_startup_live_account_is_idempotent(session_factory, tmp_path):
    create_app(make_live_deps(session_factory, tmp_path, default_live_handler),
               start_scheduler=False)
    create_app(make_live_deps(session_factory, tmp_path, default_live_handler),
               start_scheduler=False)
    with session_factory() as s:
        rows = s.scalars(select(Account).where(Account.mode == "live")).all()
        assert len(rows) == 1
