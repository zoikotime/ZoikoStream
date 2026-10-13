"""Alembic environment for the Guide database.

The URL comes from GUIDE_DATABASE_URL (or -x url=... for tests), never from DATABASE_URL, and
the version table is guide_alembic_version so this can never be confused with — or applied
to — the platform schema.
"""
from alembic import context
from sqlalchemy import create_engine, pool

from app.guide import models  # noqa: F401  (registers tables on GuideBase.metadata)
from app.guide.db import GuideBase, engine_url
from app.guide.knowledge import models as rag_models  # noqa: F401  (schema guide_rag)
from app.guide.settings import get_settings

config = context.config
target_metadata = GuideBase.metadata
VERSION_TABLE = "guide_alembic_version"
SCHEMAS = {None, "public", "guide_rag"}


def _include_object(obj, name, type_, reflected, compare_to):
    # Compare only Guide-owned objects: guide_* tables and the guide_rag schema.
    if type_ == "table":
        return obj.schema in SCHEMAS and (obj.schema == "guide_rag"
                                          or name.startswith("guide_"))
    return True


def _url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or \
        get_settings().GUIDE_DATABASE_URL
    if not url:
        raise SystemExit("GUIDE_DATABASE_URL is not set")
    return engine_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      version_table=VERSION_TABLE, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          version_table=VERSION_TABLE, compare_type=True,
                          include_schemas=True, include_object=_include_object,
                          transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
