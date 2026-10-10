"""DQ checks must pass on clean data and FAIL when data is deliberately broken (each case rolled back)."""
import pytest
from sqlalchemy import text

from sca import db, dq

from .conftest import scalar


def run_with(engine, statements, layers):
    """Apply `statements`, run checks inside the same transaction, always roll back."""
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            for s in statements:
                conn.exec_driver_sql(s)
            return {r.check_id: r for r in dq.run_checks(layers, conn=conn, raise_on_critical=False)}
        finally:
            tx.rollback()


def test_clean_pipeline_has_no_critical_failures(built, engine):
    res = dq.run_checks(raise_on_critical=False)
    bad = [r for r in res if not r.passed and r.severity == "critical"]
    assert bad == [], dq.summarize(bad)
    assert len(res) >= 40


def test_warn_checks_surface_flagged_rows_without_failing_the_run(built, engine):
    res = {r.check_id: r for r in dq.run_checks(raise_on_critical=False)}
    assert res["STG_DELIVERED_MISSING_DATE"].value == 2 and not res["STG_DELIVERED_MISSING_DATE"].passed
    assert res["STG_DATE_APPROVED_BEFORE_PURCHASE"].value == 1
    assert res["STG_DATE_CARRIER_AFTER_DELIVERY"].value == 1
    dq.run_checks()   # warn failures must not raise


def test_results_are_persisted(built, engine):
    rid = "persist-test"
    dq.run_checks(run_id=rid, raise_on_critical=False)
    assert scalar(engine, f"SELECT count(*) FROM dq.result WHERE run_id = '{rid}'") >= 40


def test_duplicate_key_is_caught(built, engine):
    res = run_with(engine, [
        "ALTER TABLE staging.orders DROP CONSTRAINT orders_pkey",
        "INSERT INTO staging.orders SELECT * FROM staging.orders LIMIT 1"], ["staging"])
    assert not res["STG_ORDERS_PK_UNIQUE"].passed and res["STG_ORDERS_PK_UNIQUE"].value == 1
    assert not res["STG_RECON_ORDERS"].passed          # row-count reconciliation breaks too


def test_null_key_is_caught(built, engine):
    res = run_with(engine, [
        "ALTER TABLE staging.orders DROP CONSTRAINT orders_pkey",
        "UPDATE staging.orders SET customer_id = NULL WHERE order_id = (SELECT min(order_id) FROM staging.orders)"],
        ["staging"])
    assert not res["STG_ORDERS_NOT_NULL_KEYS"].passed


def test_orphan_foreign_key_is_caught(built, engine):
    res = run_with(engine, [
        "INSERT INTO staging.order_items SELECT 'ghost', item_no, product_id, seller_id, shipping_limit_ts, price, "
        "freight_value FROM staging.order_items LIMIT 1"], ["staging"])
    assert not res["STG_ITEMS_ORPHAN_ORDER"].passed
    assert not res["STG_RECON_ORDER_ITEMS"].passed


def test_negative_price_is_caught(built, engine):
    res = run_with(engine, [
        "UPDATE staging.order_items SET price = -1 WHERE (order_id, item_no) = "
        "(SELECT order_id, item_no FROM staging.order_items LIMIT 1)"], ["staging"])
    assert not res["STG_ITEMS_PRICE_POSITIVE"].passed


def test_silently_dropped_rejects_are_caught(built, engine):
    res = run_with(engine, ["DELETE FROM staging.quarantine WHERE source_table = 'orders'"], ["staging"])
    assert not res["STG_RECON_ORDERS"].passed


def test_core_row_loss_is_caught(built, engine):
    res = run_with(engine, [
        "DELETE FROM core.fact_order_line WHERE (order_id, item_no) = "
        "(SELECT order_id, item_no FROM core.fact_order_line LIMIT 1)"], ["core"])
    assert not res["CORE_RECON_ORDER_LINES"].passed
    assert not res["CORE_RECON_REVENUE"].passed


def test_bad_kpi_value_is_caught(built, engine):
    res = run_with(engine, ["UPDATE core.fact_shipment SET days_late = -3 WHERE is_on_time"], ["core"])
    assert not res["CORE_SHIPMENT_DAYS_LATE_NONNEG"].passed


def test_check_that_cannot_run_counts_as_failure(built, engine):
    res = run_with(engine, ["DROP TABLE core.fact_purchase_order CASCADE"], ["core"])
    r = res["CORE_PO_RECEIPT_AFTER_ORDER"]
    assert not r.passed and r.error


def test_critical_failure_raises_and_is_recorded(built, engine):
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO staging.order_items SELECT 'ghost', item_no, product_id, seller_id, "
                          "shipping_limit_ts, price, freight_value FROM staging.order_items LIMIT 1")
    try:
        with pytest.raises(dq.DQFailure) as ei:
            dq.run_checks(["staging"], run_id="fail-test")
        assert "STG_ITEMS_ORPHAN_ORDER" in str(ei.value)
        assert scalar(engine, "SELECT count(*) FROM dq.result WHERE run_id = 'fail-test' AND NOT passed "
                              "AND severity = 'critical'") >= 1
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("DELETE FROM staging.order_items WHERE order_id = 'ghost'")
