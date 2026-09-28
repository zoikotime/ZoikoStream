"""The PostgreSQL driver is chosen by this project, not by SQLAlchemy's default.

SQLAlchemy 2.1 switched a plain `postgresql://` URL from psycopg2 to psycopg (v3), which is
not installed - CI's "Prepare the CI database" step died on
`ModuleNotFoundError: No module named 'psycopg'`. See app/dburl.py.
"""
import pytest

from app.dburl import PSYCOPG2_SCHEME, engine_url


@pytest.mark.parametrize("url", [
    "postgresql://zoiko:zoiko@127.0.0.1:5432/zoikostream_ci",
    "postgres://u:p@host/db",
    "POSTGRESQL://u:p@host/db",
])
def test_plain_urls_get_psycopg2(url):
    out = engine_url(url)
    assert out.startswith(PSYCOPG2_SCHEME)
    assert out.split("://", 1)[1] == url.split("://", 1)[1], "only the scheme changes"


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://u:p@host/db",
    "postgresql+psycopg://u:p@host/db",
    "sqlite://",
])
def test_explicit_drivers_and_other_databases_are_untouched(url):
    assert engine_url(url) == url


def test_the_application_engine_uses_psycopg2():
    from app.db import engine
    assert engine.dialect.driver == "psycopg2"
