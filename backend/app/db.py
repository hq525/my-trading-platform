from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_url: str):
    engine = create_engine(
        db_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


# Columns added after a table first shipped; create_all() will not alter
# existing tables, so init_db adds them by hand. (table, column, DDL type).
_NEW_COLUMNS = [
    ("accounts", "mode", "VARCHAR DEFAULT 'paper'"),
    ("accounts", "last_synced_at", "DATETIME"),
    ("accounts", "sync_detail", "VARCHAR"),
    ("orders", "broker_order_id", "VARCHAR"),
]


def init_db(engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, ddl in _NEW_COLUMNS:
            cols = {row[1] for row in
                    conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
