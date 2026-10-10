import pandas as pd
import pytest

from sca import db

from .conftest import SAMPLE_DIR, scalar


@pytest.fixture(scope="session")
def core(staged):
    db.run_sql_dir("20_core_dims")
    db.run_sql_dir("30_core_facts")
    return True


def test_fact_rows_reconcile_to_staging(core, engine):
    assert scalar(engine, "SELECT count(*) FROM core.fact_order_line") == scalar(engine, "SELECT count(*) FROM staging.order_items")
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment") == scalar(engine, "SELECT count(*) FROM staging.orders")


@pytest.mark.parametrize("table,key", [
    ("dim_product", "product_id"), ("dim_supplier", "seller_id"), ("dim_customer", "customer_id"),
    ("dim_warehouse", "warehouse_code"), ("dim_date", "date"), ("fact_shipment", "order_id"),
])
def test_natural_keys_unique(core, engine, table, key):
    assert scalar(engine, f'SELECT count(*) - count(DISTINCT "{key}") FROM core.{table}') == 0


def test_foreign_keys_are_enforced_in_catalog(core, engine):
    n = scalar(engine, "SELECT count(*) FROM information_schema.table_constraints "
                       "WHERE constraint_schema='core' AND constraint_type='FOREIGN KEY'")
    assert n >= 10


def test_dim_date_covers_every_fact_date(core, engine):
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment s LEFT JOIN core.dim_date d "
                          "ON d.date_key = s.purchase_date_key WHERE d.date_key IS NULL") == 0
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment WHERE estimated_ts::date > "
                          "(SELECT max(date) FROM core.dim_date)") == 0


def test_every_customer_has_a_warehouse(core, engine):
    assert scalar(engine, "SELECT count(*) FROM core.dim_customer WHERE warehouse_key IS NULL") == 0


def test_category_translated_and_unknown_kept(core, engine):
    assert scalar(engine, "SELECT category_en FROM core.dim_product WHERE product_id = 'p0000'") == "unknown"
    assert scalar(engine, "SELECT count(*) FROM core.dim_product WHERE category_en IS NULL") == 0


def test_on_time_matches_independent_pandas_recompute(core, engine):
    o = pd.read_csv(SAMPLE_DIR / "olist_orders_dataset.csv", parse_dates=[
        "order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"])
    o = o[(o.order_status == "delivered") & o.order_delivered_customer_date.notna()]
    o = o[o.order_delivered_customer_date >= o.order_purchase_timestamp]      # quarantine rule
    expected = (o.order_delivered_customer_date.dt.normalize() <= o.order_estimated_delivery_date.dt.normalize())
    got = scalar(engine, "SELECT avg(is_on_time::int) FROM core.fact_shipment WHERE is_on_time IS NOT NULL")
    assert float(got) == pytest.approx(expected.mean())
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment WHERE is_on_time IS NOT NULL") == len(o)


def test_late_days_are_non_negative_and_consistent(core, engine):
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment WHERE days_late < 0") == 0
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment WHERE is_on_time AND days_late > 0") == 0
    assert scalar(engine, "SELECT count(*) FROM core.fact_shipment WHERE NOT is_on_time AND days_late = 0") == 0


def test_order_value_reconciles(core, engine):
    a = scalar(engine, "SELECT sum(order_value) FROM core.fact_shipment")
    b = scalar(engine, "SELECT sum(price) FROM core.fact_order_line")
    assert a == b


def test_core_is_idempotent(core, engine):
    before = scalar(engine, "SELECT count(*) FROM core.fact_order_line")
    db.run_sql_dir("20_core_dims")
    db.run_sql_dir("30_core_facts")
    assert scalar(engine, "SELECT count(*) FROM core.fact_order_line") == before
