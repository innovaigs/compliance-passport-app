import os
import sys
import time
import json
import urllib.request
import subprocess
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


def test_process_smoke_validation(tmp_path):
    """Process-level smoke test validating Uvicorn startup, /api/health, root HTML, and SQLite schema creation."""
    data_dir = tmp_path / "data"
    uploads_dir = tmp_path / "uploads"
    exports_dir = tmp_path / "exports"
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>Smoke Test Shell</body></html>")

    db_path = data_dir / "smoke_test.db"

    env = os.environ.copy()
    env["COMPLIANCE_DATA_DIR"] = str(data_dir)
    env["COMPLIANCE_UPLOADS_DIR"] = str(uploads_dir)
    env["COMPLIANCE_EXPORTS_DIR"] = str(exports_dir)
    env["COMPLIANCE_FRONTEND_DIST_DIR"] = str(dist_dir)
    env["COMPLIANCE_SQLITE_DB_PATH"] = str(db_path)

    uvicorn_bin = BASE_DIR / ".venv" / "bin" / "uvicorn"
    cmd = [str(uvicorn_bin), "main:app", "--host", "127.0.0.1", "--port", "8009"]

    proc = subprocess.Popen(cmd, cwd=BASE_DIR, env=env)
    try:
        health_url = "http://127.0.0.1:8009/api/health"
        root_url = "http://127.0.0.1:8009/"

        healthy = False
        for _ in range(20):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(health_url) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("status") == "healthy":
                            healthy = True
                            break
            except Exception:
                pass

        assert healthy is True, "Uvicorn process did not become healthy within timeout"

        # Verify root HTML serving
        with urllib.request.urlopen(root_url) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "Smoke Test Shell" in html

        # Verify SQLite schema created
        assert db_path.exists(), "SQLite database file was not created"

    finally:
        proc.terminate()
        proc.wait(timeout=5)
