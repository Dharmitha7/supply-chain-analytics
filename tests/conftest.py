"""Tests run against a dedicated database (<POSTGRES_DB>_test) so the real pipeline data is never touched."""
import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_DB = os.environ.get("SCA_TEST_DB", os.environ.get("POSTGRES_DB", "supply_chain") + "_test")
os.environ["POSTGRES_DB"] = TEST_DB  # must be set before sca.config/db are used

SAMPLE_DIR = ROOT / "data" / "sample"


def _ensure_test_db() -> None:
    g = os.environ.get
    dsn = (f"host={g('POSTGRES_HOST', 'localhost')} port={g('POSTGRES_PORT', '5432')} "
           f"user={g('POSTGRES_USER')} password={g('POSTGRES_PASSWORD')} dbname=postgres")
    with psycopg.connect(dsn, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
            c.execute(f'CREATE DATABASE "{TEST_DB}"')


@pytest.fixture(scope="session")
def engine():
    _ensure_test_db()
    from sca import db
    return db.engine()


@pytest.fixture(scope="session")
def raw_loaded(engine):
    from sca import ingest
    return ingest.run(SAMPLE_DIR)


@pytest.fixture(scope="session")
def staged(raw_loaded):
    from sca import db
    db.run_sql_dir("10_staging")
    return True


def scalar(engine, sql: str):
    from sqlalchemy import text
    with engine.connect() as c:
        return c.execute(text(sql)).scalar()
