from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB_PATH: Final[Path] = Path.home() / ".queuectl" / "queuectl.db"


class Base(DeclarativeBase):
    pass


def sqlite_url_for(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    return f"sqlite:///{resolved.as_posix()}"


def get_database_url() -> str:
    configured = os.environ.get("QUEUECTL_DATABASE_URL")
    if configured:
        return configured
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_url_for(DEFAULT_DB_PATH)


def create_engine_for_url(database_url: str | None = None) -> Engine:
    url = database_url or get_database_url()
    connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, connect_args=connect_args, future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


def init_db(engine: Engine) -> None:
    from queuectl.storage import orm  # noqa: F401

    Base.metadata.create_all(engine)

