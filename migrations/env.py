"""Alembic environment. Migrations connect as the schema owner: MIGRATE_DATABASE_URL if
set (prod and tests: the owner role), else the app's DATABASE_URL (a dev database where one
role does everything)."""

import logging
import os
from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import create_engine, pool

# migrations/ is put on sys.path by alembic.ini (prepend_sys_path), for every command.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
logger = logging.getLogger("alembic.env")

target_metadata = current_app.extensions["migrate"].db.metadata


def migration_url():
    return os.environ.get("MIGRATE_DATABASE_URL") or current_app.config["SQLALCHEMY_DATABASE_URI"]


def require_utf8(url):
    """Refuse a database that isn't UTF8 before SQLAlchemy touches it. On SQL_ASCII,
    psycopg returns bytes for text and SQLAlchemy dies with a TypeError during connect;
    worse, Postgres would store customer text unvalidated. Checked with a raw psycopg
    connection, because the SQLAlchemy one can't even be opened."""
    import psycopg
    from sqlalchemy.engine import make_url

    libpq = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(libpq, connect_timeout=10) as conn:
        encoding = conn.info.parameter_status("server_encoding")
        database = conn.info.dbname
    if encoding != "UTF8":
        raise RuntimeError(
            f"database {database!r} has encoding {encoding}; shop-hub needs UTF8. It was "
            "probably created on a cluster without a UTF-8 locale. If it's empty, drop it and "
            "recreate it with: CREATE DATABASE ... TEMPLATE template0 ENCODING 'UTF8' "
            "LOCALE 'C.UTF-8' (see deploy/sql/create_roles.psql)."
        )


def run_migrations_offline():
    context.configure(
        url=migration_url(), target_metadata=target_metadata, literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, "autogenerate", False) and directives[0].upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No changes in schema detected.")

    require_utf8(migration_url())
    engine = create_engine(migration_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            process_revision_directives=process_revision_directives,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
