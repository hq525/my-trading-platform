from sqlalchemy import text

from app.db import make_engine


def test_make_engine_enables_wal(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"
