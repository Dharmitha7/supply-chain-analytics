import pytest
from sqlalchemy import text

from sca import db
from sca.make_sample import EXPECTED_FLAGS, EXPECTED_QUARANTINE

from .conftest import scalar

TABLES = ["customers", "sellers", "products", "orders", "order_items", "order_reviews", "order_payments"]


def _quarantine(engine) -> dict:
    with engine.connect() as c:
        rows = c.execute(text("SELECT source_table, reason_code, count(*) FROM staging.quarantine GROUP BY 1, 2"))
        return {(s, r): n for s, r, n in rows}


def test_each_cleaning_rule_fires_with_expected_counts(staged, engine):
    assert _quarantine(engine) == EXPECTED_QUARANTINE


@pytest.mark.parametrize("flag,expected", EXPECTED_FLAGS.items())
def test_soft_anomalies_are_flagged_not_dropped(staged, engine, flag, expected):
    assert scalar(engine, f"SELECT count(*) FROM staging.orders WHERE {flag}") == expected


@pytest.mark.parametrize("table", TABLES)
def test_raw_equals_kept_plus_quarantined(staged, engine, table):
    raw = scalar(engine, f"SELECT count(*) FROM raw.{table}")
    kept = scalar(engine, f"SELECT count(*) FROM staging.{table}")
    quarantined = scalar(engine, f"SELECT count(*) FROM staging.quarantine WHERE source_table = '{table}'")
    assert raw == kept + quarantined


def test_missing_category_becomes_unknown(staged, engine):
    assert scalar(engine, "SELECT count(*) FROM staging.products WHERE category_pt = 'unknown'") == 1
    assert scalar(engine, "SELECT count(*) FROM staging.products WHERE category_was_missing") == 1


def test_zero_weight_nulled_not_dropped(staged, engine):
    assert scalar(engine, "SELECT weight_g FROM staging.products WHERE product_id = 'p0001'") is None


def test_no_bad_values_survive(staged, engine):
    assert scalar(engine, "SELECT count(*) FROM staging.order_items WHERE price <= 0 OR freight_value < 0") == 0
    assert scalar(engine, "SELECT count(*) FROM staging.orders WHERE delivered_ts < purchase_ts") == 0
    assert scalar(engine, "SELECT count(*) FROM staging.orders WHERE status IN ('canceled','unavailable') "
                          "AND delivered_ts IS NOT NULL") == 0


def test_no_orphans_in_staging(staged, engine):
    assert scalar(engine, "SELECT count(*) FROM staging.order_items i LEFT JOIN staging.orders o USING (order_id) "
                          "WHERE o.order_id IS NULL") == 0


def test_one_review_per_order_and_latest_kept(staged, engine):
    assert scalar(engine, "SELECT count(*) - count(DISTINCT order_id) FROM staging.order_reviews") == 0
    assert scalar(engine, "SELECT count(*) FROM staging.order_reviews WHERE review_id LIKE 'old%'") == 0


def test_geolocation_collapsed_to_one_row_per_zip(staged, engine):
    assert scalar(engine, "SELECT count(*) - count(DISTINCT zip_prefix) FROM staging.geolocation") == 0
    assert scalar(engine, "SELECT count(*) FROM staging.geolocation") == 50


def test_staging_is_idempotent(staged, engine):
    before = _quarantine(engine)
    db.run_sql_dir("10_staging")
    assert _quarantine(engine) == before
    assert scalar(engine, "SELECT count(*) FROM staging.orders") == scalar(
        engine, "SELECT count(*) - 3 FROM raw.orders")
