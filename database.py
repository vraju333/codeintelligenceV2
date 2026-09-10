import logging
from typing import Any

from sqlalchemy import create_engine, delete, insert, inspect, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config import settings


logger = logging.getLogger(__name__)
Base = declarative_base()


def _sqlite_connect_args(url: str) -> dict[str, Any]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


def _create_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=not url.startswith("sqlite"),
        connect_args=_sqlite_connect_args(url),
    )


def _resolve_primary_url() -> str:
    mode = settings.DATABASE_MODE

    if mode == "sqlite":
        return settings.LOCAL_DATABASE_URL

    if mode == "postgres":
        if not settings.POSTGRES_DATABASE_URL:
            raise RuntimeError(
                "DATABASE_MODE=postgres but POSTGRES_DATABASE_URL/DATABASE_URL is not configured."
            )
        return settings.POSTGRES_DATABASE_URL

    if mode == "dual":
        # Local SQLite deliberately remains primary so PostgreSQL availability
        # never breaks the hackathon/demo.
        return settings.LOCAL_DATABASE_URL

    raise RuntimeError(
        f"Unsupported DATABASE_MODE={mode!r}. Use sqlite, postgres, or dual."
    )


PRIMARY_DATABASE_URL = _resolve_primary_url()
engine = _create_engine(PRIMARY_DATABASE_URL)

mirror_engine: Engine | None = None
mirror_available = False

if settings.DATABASE_MODE == "dual" and settings.POSTGRES_DATABASE_URL:
    try:
        mirror_engine = _create_engine(settings.POSTGRES_DATABASE_URL)
        # Test the connection without making application startup depend on it.
        with mirror_engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        mirror_available = True
        logger.info("Dual DB mode: PostgreSQL mirror is available.")
    except Exception as exc:
        mirror_engine = None
        mirror_available = False
        logger.warning(
            "Dual DB mode: PostgreSQL mirror unavailable. "
            "CodeIntelligence will continue using local SQLite. Reason: %s",
            exc,
        )


def _pk_filter(table, row: dict[str, Any]):
    conditions = []
    for column in table.primary_key.columns:
        conditions.append(column == row[column.name])
    return conditions


def _row_snapshot(obj: Any) -> tuple[Any, dict[str, Any]] | None:
    mapper = inspect(obj).mapper
    table = mapper.local_table
    row = {}
    for column in table.columns:
        row[column.name] = getattr(obj, column.name)
    return table, row


def _mirror_changes(
    upserts: list[tuple[Any, dict[str, Any]]],
    deletes: list[tuple[Any, dict[str, Any]]],
) -> None:
    """Best-effort row-level mirror. Local DB success is never rolled back."""
    if not mirror_available or mirror_engine is None:
        return

    try:
        with mirror_engine.begin() as connection:
            # Deletes first so explicit removals are reflected.
            for table, row in deletes:
                pk_conditions = _pk_filter(table, row)
                if pk_conditions:
                    connection.execute(delete(table).where(*pk_conditions))

            for table, row in upserts:
                pk_conditions = _pk_filter(table, row)

                existing = None
                if pk_conditions:
                    existing = connection.execute(
                        select(*table.primary_key.columns).where(*pk_conditions).limit(1)
                    ).first()

                if existing:
                    non_pk = {
                        key: value
                        for key, value in row.items()
                        if key not in {c.name for c in table.primary_key.columns}
                    }
                    if non_pk:
                        connection.execute(
                            update(table).where(*pk_conditions).values(**non_pk)
                        )
                else:
                    connection.execute(insert(table).values(**row))
    except Exception as exc:
        logger.warning(
            "PostgreSQL mirror write failed; local SQLite data is safe. Reason: %s",
            exc,
        )


class DualWriteSession(Session):
    """
    SQLite is the authoritative transaction in dual mode.
    After a successful local commit, changed rows are mirrored to PostgreSQL.
    Mirror failures are logged but do not fail the request.
    """

    def commit(self):
        if settings.DATABASE_MODE != "dual":
            return super().commit()

        # flush assigns generated primary keys before we snapshot rows
        self.flush()

        upserts = []
        deletes = []

        for obj in list(self.new) + list(self.dirty):
            try:
                snapshot = _row_snapshot(obj)
                if snapshot:
                    upserts.append(snapshot)
            except Exception:
                logger.debug("Skipping unsupported mirror object %r", obj, exc_info=True)

        for obj in list(self.deleted):
            try:
                snapshot = _row_snapshot(obj)
                if snapshot:
                    deletes.append(snapshot)
            except Exception:
                logger.debug("Skipping unsupported mirror delete %r", obj, exc_info=True)

        result = super().commit()
        _mirror_changes(upserts, deletes)
        return result


SessionClass = DualWriteSession if settings.DATABASE_MODE == "dual" else Session

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=SessionClass,
    expire_on_commit=False,
)


def initialize_storage() -> None:
    """
    Create tables in the primary DB and, when available, the PostgreSQL mirror.
    ORM models must be imported before this function is called.
    """
    Base.metadata.create_all(bind=engine)

    if mirror_available and mirror_engine is not None:
        try:
            Base.metadata.create_all(bind=mirror_engine)
        except Exception as exc:
            logger.warning("Could not create mirror tables in PostgreSQL: %s", exc)


def storage_status() -> dict[str, Any]:
    return {
        "mode": settings.DATABASE_MODE,
        "primary": PRIMARY_DATABASE_URL.split("://", 1)[0],
        "local_database_url": settings.LOCAL_DATABASE_URL,
        "postgres_configured": bool(settings.POSTGRES_DATABASE_URL),
        "postgres_mirror_available": mirror_available,
    }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
