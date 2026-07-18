from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from queuectl.storage.database import create_engine_for_url, create_session_factory, init_db, sqlite_url_for


def shell_command(*parts: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


@pytest.fixture()
def db_url(tmp_path) -> str:
    return sqlite_url_for(tmp_path / "queuectl.db")


@pytest.fixture()
def session_factory(db_url: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine_for_url(db_url)
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()

