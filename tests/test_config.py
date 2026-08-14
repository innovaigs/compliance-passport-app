import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from config import Settings, get_settings, validate_and_bootstrap_storage
from main import app


def test_default_settings():
    """Verify default settings instantiation and path structure."""
    settings = get_settings()
    assert settings.app_name == "Compliance Passport"
    assert isinstance(settings.data_dir, Path)
    assert isinstance(settings.uploads_dir, Path)
    assert isinstance(settings.questionnaires_dir, Path)
    assert isinstance(settings.exports_dir, Path)
    assert isinstance(settings.frontend_dist_dir, Path)


def test_environment_overrides(monkeypatch, tmp_path):
    """Verify environment variable overrides for runtime storage paths."""
    custom_data = tmp_path / "custom_data"
    custom_uploads = tmp_path / "custom_uploads"
    custom_exports = tmp_path / "custom_exports"

    monkeypatch.setenv("COMPLIANCE_DATA_DIR", str(custom_data))
    monkeypatch.setenv("COMPLIANCE_UPLOADS_DIR", str(custom_uploads))
    monkeypatch.setenv("COMPLIANCE_EXPORTS_DIR", str(custom_exports))

    settings = get_settings()
    assert settings.data_dir == custom_data
    assert settings.uploads_dir == custom_uploads
    assert settings.exports_dir == custom_exports


def test_directory_bootstrap(tmp_path):
    """Verify validate_and_bootstrap_storage creates missing directories."""
    base = tmp_path / "app_root"
    settings = Settings(
        base_dir=base,
        data_dir=base / "data",
        uploads_dir=base / "uploads" / "evidence",
        questionnaires_dir=base / "uploads" / "questionnaires",
        exports_dir=base / "exports",
        frontend_dist_dir=base / "frontend" / "dist",
        sqlite_db_path=base / "data" / "compliance.db",
    )

    assert not settings.data_dir.exists()
    assert not settings.uploads_dir.exists()
    assert not settings.questionnaires_dir.exists()
    assert not settings.exports_dir.exists()

    bootstrapped = validate_and_bootstrap_storage(settings)

    assert bootstrapped.data_dir.exists()
    assert bootstrapped.uploads_dir.exists()
    assert bootstrapped.questionnaires_dir.exists()
    assert bootstrapped.exports_dir.exists()


def test_unsafe_static_root_nesting_rejection(tmp_path):
    """Verify ValueError is raised if upload or export path is nested inside frontend_dist."""
    base = tmp_path / "app_root"
    dist = base / "frontend" / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    # Configure uploads_dir inside frontend_dist
    unsafe_settings = Settings(
        base_dir=base,
        data_dir=base / "data",
        uploads_dir=dist / "unsafe_uploads",
        questionnaires_dir=base / "uploads" / "questionnaires",
        exports_dir=base / "exports",
        frontend_dist_dir=dist,
        sqlite_db_path=base / "data" / "compliance.db",
    )

    with pytest.raises(ValueError, match="Unsafe path configuration"):
        validate_and_bootstrap_storage(unsafe_settings)


def test_app_startup_integration_with_storage_bootstrap(monkeypatch, tmp_path):
    """Integration test verifying app startup bootstraps paths and /api/health responds."""
    test_base = tmp_path / "integration_root"
    monkeypatch.setenv("COMPLIANCE_DATA_DIR", str(test_base / "data"))
    monkeypatch.setenv("COMPLIANCE_UPLOADS_DIR", str(test_base / "uploads"))
    monkeypatch.setenv("COMPLIANCE_EXPORTS_DIR", str(test_base / "exports"))

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    assert (test_base / "data").exists()
    assert (test_base / "uploads").exists()
    assert (test_base / "exports").exists()
