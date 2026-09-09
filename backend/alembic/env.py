from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app import models  # noqa: F401
from app.database import Base
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

configured_url = config.attributes.get("database_url")
if configured_url:
    config.set_main_option("sqlalchemy.url", str(configured_url))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        try:
            context.configure(
                connection=connection, target_metadata=target_metadata, render_as_batch=True
            )
            with context.begin_transaction():
                context.run_migrations()
            if connection.dialect.name == "sqlite":
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                if violations:
                    raise RuntimeError(f"Migration produced foreign-key violations: {violations!r}")
        finally:
            if connection.dialect.name == "sqlite":
                connection.rollback()
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
                if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                    raise RuntimeError("Migrations left SQLite foreign-key enforcement disabled.")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
