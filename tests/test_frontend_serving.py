import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app


def test_frontend_serving_with_build(tmp_path, monkeypatch):
    """Test root HTML, SPA route fallback, static assets, and API precedence when dist exists."""
    dist_dir = tmp_path / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    index_html = dist_dir / "index.html"
    index_html.write_text("<!DOCTYPE html><html><body>Compliance Passport App</body></html>")

    asset_file = assets_dir / "main.js"
    asset_file.write_text("console.log('Compliance Passport Loaded');")

    monkeypatch.setenv("COMPLIANCE_FRONTEND_DIST_DIR", str(dist_dir))

    with TestClient(app) as client:
        # GET / returns index.html
        resp_root = client.get("/")
        assert resp_root.status_code == 200
        assert "Compliance Passport App" in resp_root.text

        # GET /runs/example-review returns index.html (SPA fallback)
        resp_spa = client.get("/runs/example-review")
        assert resp_spa.status_code == 200
        assert "Compliance Passport App" in resp_spa.text

        # GET /assets/main.js returns static asset
        resp_asset = client.get("/assets/main.js")
        assert resp_asset.status_code == 200
        assert "console.log" in resp_asset.text

        # GET /api/health returns health JSON
        resp_health = client.get("/api/health")
        assert resp_health.status_code == 200
        assert resp_health.json()["status"] == "healthy"

        # GET /api/unknown returns 404 JSON, not HTML
        resp_unknown_api = client.get("/api/unknown")
        assert resp_unknown_api.status_code == 404
        assert resp_unknown_api.json()["detail"] == "Not Found"


def test_frontend_serving_missing_build(tmp_path, monkeypatch):
    """Test 404 JSON response when frontend dist directory does not exist or has no index.html."""
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("COMPLIANCE_FRONTEND_DIST_DIR", str(empty_dist))

    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Frontend build not found"

        resp_health = client.get("/api/health")
        assert resp_health.status_code == 200
        assert resp_health.json()["status"] == "healthy"
