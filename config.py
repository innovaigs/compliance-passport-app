"""
Runtime configuration and storage directory bootstrap for Compliance Passport.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    app_name: str = "Compliance Passport"
    app_version: str = "1.0.0"

    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    uploads_dir: Path = BASE_DIR / "uploads" / "evidence"
    questionnaires_dir: Path = BASE_DIR / "uploads" / "questionnaires"
    exports_dir: Path = BASE_DIR / "exports"
    frontend_dist_dir: Path = BASE_DIR / "frontend" / "dist"
    sqlite_db_path: Path = BASE_DIR / "data" / "compliance.db"

    model_config = SettingsConfigDict(
        env_prefix="COMPLIANCE_",
        env_file=".env",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Returns a Settings instance populated from defaults and environment overrides."""
    return Settings()


def validate_and_bootstrap_storage(settings: Optional[Settings] = None) -> Settings:
    """
    Validates storage paths and creates required directories.
    Ensures upload, questionnaire, export, and data paths are not nested under frontend_dist.
    """
    if settings is None:
        settings = get_settings()

    # Resolve frontend_dist_dir absolute path
    frontend_dist = settings.frontend_dist_dir.resolve()

    restricted_paths = {
        "uploads_dir": settings.uploads_dir,
        "questionnaires_dir": settings.questionnaires_dir,
        "exports_dir": settings.exports_dir,
        "data_dir": settings.data_dir,
    }

    for label, path in restricted_paths.items():
        resolved_path = path.resolve()
        # If the path equals frontend_dist or is a subpath of frontend_dist
        if resolved_path == frontend_dist or frontend_dist in resolved_path.parents:
            raise ValueError(
                f"Unsafe path configuration: {label} ({resolved_path}) cannot be nested inside frontend_dist_dir ({frontend_dist})"
            )

    # Create missing writable runtime directories
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.questionnaires_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    return settings
