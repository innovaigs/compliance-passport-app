"""
Alembic environment for Compliance Passport.

The database URL is resolved from config.Settings at runtime, not from
alembic.ini, so that a COMPLIANCE_SQLITE_DB_PATH override applies identically to
migrations and to the application. If the URL cannot be resolved, this raises —
migrations never guess at a target database.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import get_settings
from database import Base
import models  # noqa: F401  — registers every model on Base.metadata

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers must stay False. init_db runs migrations inside
    # the application's startup, and the default (True) would switch off
    # uvicorn's loggers — turning a failed migration into a bare non-zero exit
    # with no message on the way out.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# The FTS5 virtual table and the shadow tables SQLite creates for it are not in
# Base.metadata, so autogenerate sees them as tables to DROP. It has already
# emitted exactly that once, and applying it destroyed the retrieval index.
# Anything managed by raw SQL in a migration must be hidden from autogenerate.
FTS_TABLE_PREFIX = "evidence_chunks_fts"


def include_name(name, type_, parent_names):
    if type_ == "table" and name and name.startswith(FTS_TABLE_PREFIX):
        return False
    return True


def get_url() -> str:
    """
    Resolves the target database URL.

    A URL set programmatically on the Config wins (database.init_db does this
    when migrating a caller-supplied engine). Otherwise it comes from
    application settings, so migrations and the app always agree. Raises rather
    than defaulting to a guessed path.
    """
    explicit = config.get_main_option("sqlalchemy.url", None)
    if explicit:
        return explicit

    db_path = get_settings().sqlite_db_path
    if not db_path:
        raise RuntimeError(
            "sqlite_db_path is not configured; refusing to run migrations "
            "against an unknown database."
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the
            # table instead. Required for every migration after the baseline.
            render_as_batch=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
