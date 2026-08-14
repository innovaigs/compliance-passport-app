"""
Foundation Runtime Automated Validation Script for Compliance Passport.
"""

import os
import sys
import time
import subprocess
import tempfile
import urllib.request
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def run_cmd(cmd, cwd=BASE_DIR, env=None):
    """Executes a command and raises RuntimeError if non-zero exit code."""
    print(f"--> Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    res = subprocess.run(cmd, cwd=cwd, env=env or os.environ, shell=isinstance(cmd, str))
    if res.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {res.returncode}: {cmd}")


def validate_backend_tests():
    """Runs pytest test suite."""
    print("\n=== 1. Validating Pytest Test Suite ===")
    pytest_bin = BASE_DIR / ".venv" / "bin" / "pytest"
    cmd = [str(pytest_bin)] if pytest_bin.exists() else [sys.executable, "-m", "pytest"]
    run_cmd(cmd)


def validate_process_smoke():
    """Starts Uvicorn in a subprocess, tests /api/health, /, and schema creation."""
    print("\n=== 2. Validating Single-Process Uvicorn Smoke Test ===")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        data_dir = tmp_path / "data"
        uploads_dir = tmp_path / "uploads"
        exports_dir = tmp_path / "exports"
        dist_dir = tmp_path / "frontend" / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>Validation App Shell</body></html>")

        db_path = data_dir / "smoke.db"

        env = os.environ.copy()
        env["COMPLIANCE_DATA_DIR"] = str(data_dir)
        env["COMPLIANCE_UPLOADS_DIR"] = str(uploads_dir)
        env["COMPLIANCE_EXPORTS_DIR"] = str(exports_dir)
        env["COMPLIANCE_FRONTEND_DIST_DIR"] = str(dist_dir)
        env["COMPLIANCE_SQLITE_DB_PATH"] = str(db_path)

        uvicorn_bin = BASE_DIR / ".venv" / "bin" / "uvicorn"
        cmd = [str(uvicorn_bin), "main:app", "--host", "127.0.0.1", "--port", "8008"]

        proc = subprocess.Popen(cmd, cwd=BASE_DIR, env=env)
        try:
            health_url = "http://127.0.0.1:8008/api/health"
            root_url = "http://127.0.0.1:8008/"

            healthy = False
            for _ in range(20):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(health_url) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            if data.get("status") == "healthy":
                                healthy = True
                                print("   [PASS] GET /api/health returned 200 OK with healthy status")
                                break
                except Exception:
                    pass

            if not healthy:
                raise RuntimeError("Uvicorn process did not become healthy on /api/health within timeout.")

            # Test root UI serving
            with urllib.request.urlopen(root_url) as resp:
                html = resp.read().decode("utf-8")
                if "Validation App Shell" in html:
                    print("   [PASS] GET / returned frontend HTML from single process")
                else:
                    raise RuntimeError("GET / did not return expected frontend HTML content.")

            # Verify SQLite schema tables created
            if db_path.exists():
                print(f"   [PASS] SQLite database created at {db_path}")
            else:
                raise RuntimeError("SQLite database file was not created by startup lifespan.")

        finally:
            proc.terminate()
            proc.wait(timeout=5)
            print("   [PASS] Process terminated cleanly.")


def main():
    print("Starting Foundation Runtime Automated Validation...")
    validate_backend_tests()
    validate_process_smoke()
    print("\nALL FOUNDATION VALIDATIONS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
