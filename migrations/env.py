"""Alembic environment. Migrations connect as the schema owner: MIGRATE_DATABASE_URL if
set (prod and tests: the owner role), else the app's DATABASE_URL (a dev database where one
role does everything)."""

import logging
import os
import sys
from logging.config import fileConfig

from alembic import context
from flask import current_app
from sqlalchemy import create_engine, pool

# migrations/ itself, so revisions can `import sqlhelpers`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
logger = logging.getLogger("alembic.env")

target_metadata = current_app.extensions["migrate"].db.metadata


def migration_url():
    return os.environ.get("MIGRATE_DATABASE_URL") or current_app.config["SQLALCHEMY_DATABASE_URI"]


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
