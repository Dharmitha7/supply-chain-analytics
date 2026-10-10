import pandas as pd

from sca.ingest import FILES

from .conftest import SAMPLE_DIR, scalar


def test_every_csv_row_lands_in_raw(raw_loaded, engine):
    for csv, table in FILES.items():
        expected = len(pd.read_csv(SAMPLE_DIR / csv, dtype=str, keep_default_na=False))
        assert scalar(engine, f"SELECT count(*) FROM raw.{table}") == expected, table


def test_raw_is_untyped_text(raw_loaded, engine):
    n = scalar(engine, "SELECT count(*) FROM information_schema.columns "
                       "WHERE table_schema='raw' AND data_type <> 'text'")
    assert n == 0


def test_ingest_is_idempotent(raw_loaded, engine):
    from sca import ingest
    before = scalar(engine, "SELECT count(*) FROM raw.orders")
    ingest.run(SAMPLE_DIR)
    assert scalar(engine, "SELECT count(*) FROM raw.orders") == before
