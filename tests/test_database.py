from sqlalchemy import text
from fastapi.testclient import TestClient
from database import create_sqlite_engine, get_db
from main import app


def test_sqlite_file_creation(temp_db_path):
    """Verify that SQLite file is created in the configured data directory."""
    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert temp_db_path.exists()
    engine.dispose()


def test_sqlite_pragmas(temp_db_engine):
    """Verify SQLite pragmas: foreign keys enabled, WAL journal mode, busy timeout 5000ms."""
    with temp_db_engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        journal = conn.execute(text("PRAGMA journal_mode;")).scalar()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()

        assert fk == 1
        assert str(journal).lower() == "wal"
        assert busy_timeout == 5000


def test_get_db_session_lifecycle(temp_db_engine, monkeypatch):
    """Verify get_db dependency yields a valid session and closes it afterwards."""
    from database import SessionLocal

    db_gen = get_db()
    session = next(db_gen)
    assert session.is_active

    result = session.execute(text("SELECT 1")).scalar()
    assert result == 1

    try:
        next(db_gen)
    except StopIteration:
        pass


def test_app_startup_with_database(temp_app_settings):
    """System integration test verifying app startup initializes database and /api/health works."""
    db_path = temp_app_settings["db_path"]

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    assert db_path.exists()
