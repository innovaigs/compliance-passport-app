"""
SQLAlchemy SQLite database engine, session factory, pragmas, and dependency management.
"""

from typing import Generator, Optional
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from config import get_settings, Settings


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


def init_db(target_engine=None):
    """Initializes the database schema by creating all tables defined on Base."""
    import models  # Ensure all model classes are registered on Base
    eng = target_engine or create_sqlite_engine()
    Base.metadata.create_all(bind=eng)
    with eng.connect() as conn:
        for stmt in (
            "ALTER TABLE questionnaire_runs ADD COLUMN error TEXT;",
            "ALTER TABLE questionnaire_runs ADD COLUMN degraded BOOLEAN NOT NULL DEFAULT 0;",
            "ALTER TABLE sandbox_events ADD COLUMN agent VARCHAR(50);",
        ):
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()
    return eng


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped SQLAlchemy session."""
    current_engine = create_sqlite_engine()
    factory = sessionmaker(autocommit=False, autoflush=False, bind=current_engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()
