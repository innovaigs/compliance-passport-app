"""
SQLAlchemy SQLite database engine, session factory, pragmas, and dependency management.
"""

from pathlib import Path
from typing import Generator, Optional, TYPE_CHECKING
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from config import get_settings, Settings

if TYPE_CHECKING:
    from alembic.config import Config


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy models."""
    pass


def create_sqlite_engine(db_path=None):
    """Creates a SQLAlchemy engine for SQLite with explicit pragmas."""
    if db_path is None:
        settings = get_settings()
        db_path = settings.sqlite_db_path

    # Ensure parent directory for SQLite file exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()

    return engine


engine = create_sqlite_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


MIGRATION_HEAD = "head"
_BASELINE_TABLE = "evidence_documents"


def _alembic_config(db_url: str) -> "Config":
    """Builds an Alembic Config pointed at alembic.ini next to this module."""
    from alembic.config import Config

    ini_path = Path(__file__).resolve().parent / "alembic.ini"
    if not ini_path.is_file():
        raise RuntimeError(
            f"alembic.ini not found at {ini_path}; cannot migrate the database."
        )
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def init_db(target_engine=None):
    """
    Brings the database to the current migration head and returns the engine.

    Three cases, all explicit:
      - no tables            -> upgrade from empty
      - tables, no version   -> pre-Alembic database, stamped to head (logged)
      - tables and a version -> normal upgrade

    Any migration failure propagates. The application must not start against a
    database whose schema it cannot account for.
    """
    from alembic import command
    from sqlalchemy import inspect

    import models  # noqa: F401 — registers every model on Base.metadata

    eng = target_engine or create_sqlite_engine()
    cfg = _alembic_config(str(eng.url))

    inspector = inspect(eng)
    tables = set(inspector.get_table_names())
    has_version = "alembic_version" in tables
    has_schema = _BASELINE_TABLE in tables

    if has_schema and not has_version:
        # Built by the pre-Alembic create_all path. Adopt it only if its schema
        # is genuinely what the migrations produce — verified, never assumed.
        _assert_schema_matches_head(eng)
        print(
            "[MIGRATIONS] Existing pre-Alembic database at "
            f"{eng.url} verified against {MIGRATION_HEAD}; stamping without "
            "altering data."
        )
        command.stamp(cfg, MIGRATION_HEAD)
        return eng

    command.upgrade(cfg, MIGRATION_HEAD)
    return eng


def _assert_schema_matches_head(eng) -> None:
    """
    Raises unless the database behind `eng` is structurally identical to a
    database built from migrations.

    The reference is produced by actually running the migrations against an
    empty temporary file, so this compares against what the migrations do, not
    against what the ORM models declare — the two can drift, and the migration
    is what governs.
    """
    import tempfile
    from alembic import command as alembic_command

    from schema_check import describe_schema_at_path, diff_schemas

    db_path = eng.url.database
    if not db_path:
        raise RuntimeError(
            f"Cannot verify schema: engine URL {eng.url} has no database file."
        )

    with tempfile.TemporaryDirectory(prefix="cp-schema-ref-") as tmp_dir:
        reference_path = Path(tmp_dir) / "reference.db"
        alembic_command.upgrade(
            _alembic_config(f"sqlite:///{reference_path}"), MIGRATION_HEAD
        )
        expected = describe_schema_at_path(reference_path)

    actual = describe_schema_at_path(db_path)
    differences = diff_schemas(expected, actual)

    if differences:
        detail = "\n".join(f"  - {line}" for line in differences)
        raise RuntimeError(
            "Refusing to adopt an existing database whose schema does not match "
            f"migration {MIGRATION_HEAD}.\n"
            f"Database: {db_path}\n"
            f"{len(differences)} difference(s) found:\n{detail}\n"
            "This database was not produced by these migrations. Reconcile it "
            "or restore from a backup; it will not be stamped as current."
        )


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped SQLAlchemy session."""
    current_engine = create_sqlite_engine()
    factory = sessionmaker(autocommit=False, autoflush=False, bind=current_engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()
