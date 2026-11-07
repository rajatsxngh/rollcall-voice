"""SQLite access. Every run (live, each test run) is its own database file with the same schema."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


def utcnow() -> datetime:
    """Timezone-aware UTC. SQLModel requires aware datetimes and stores them as UTC;
    the dashboard shows Central time. A simulated clock must return aware UTC too."""
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # treat naive values as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path | str, durable: bool = True) -> None:
        """durable=False skips syncing to disk: much faster, for throwaway simulated runs only."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        sync = "NORMAL" if durable else "OFF"
        event.listen(self.engine, "connect", lambda conn, record: _set_pragmas(conn, sync))

    def create_all(self) -> None:
        from app import models  # noqa: F401  (registers the tables)

        SQLModel.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()


def _set_pragmas(dbapi_connection, sync: str) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
    cursor.execute(f"PRAGMA synchronous={sync}")  # NORMAL: fast commits, safe with WAL
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
