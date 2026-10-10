"""Marts are validated against independent pandas recomputation from the core tables."""
import pandas as pd
import pytest

from sca import db, dq

from .conftest import scalar


@pytest.fixture(scope="session")
def marts(built):
    db.run_sql_dir("40_marts")
    return True


def q(engine, sql):
    return pd.read_sql(sql, engine)


def test_all_mart_views_exist_and_have_rows(marts, engine):
    for v in ["kpi_delivery_monthly", "delivery_by_state", "review_vs_lateness", "supplier_scorecard",
              "supplier_lead_time", "inventory_position_current", "inventory_weekly", "kpi_executive_monthly",
              "dim_date", "dim_product", "dim_supplier", "dim_warehouse", "shipments", "inventory_snapshot",
              "quarantine_summary"]:
        assert scalar(engine, f"SELECT count(*) FROM marts.{v}") > 0, v


def test_overall_otd_matches_pandas(marts, engine):
    ship = q(engine, "SELECT is_on_time, days_late FROM core.fact_shipment")
    elig = ship[ship.is_on_time.notna()]
    expected = 100 * elig.is_on_time.astype(bool).mean()
    m = q(engine, "SELECT on_time_orders, otd_eligible_orders FROM marts.kpi_delivery_monthly")
    assert 100 * m.on_time_orders.sum() / m.otd_eligible_orders.sum() == pytest.approx(expected)
    assert m.otd_eligible_orders.sum() == len(elig)


def test_fulfillment_and_cancel_rate_definitions(marts, engine):
    ship = q(engine, "SELECT is_delivered, is_canceled FROM core.fact_shipment")
    m = q(engine, "SELECT delivered_orders, non_canceled_orders, canceled_orders, orders FROM marts.kpi_delivery_monthly")
    assert m.delivered_orders.sum() == ship.is_delivered.sum()
    assert m.non_canceled_orders.sum() == (~ship.is_canceled).sum()
    assert m.canceled_orders.sum() == ship.is_canceled.sum()
    assert m.orders.sum() == len(ship)


def test_supplier_lead_time_stats_match_pandas(marts, engine):
    po = q(engine, "SELECT supplier_key, lead_days FROM core.fact_purchase_order WHERE is_received")
    exp = po.groupby("supplier_key").lead_days.agg(["count", "mean", lambda s: s.quantile(0.9), "std"])
    exp.columns = ["n", "mean", "p90", "std"]
    got = q(engine, "SELECT * FROM marts.supplier_lead_time").set_index("supplier_key")
    assert set(got.index) == set(exp.index)
    assert (got.sim_po_count.astype(float) - exp.n).abs().max() == 0
    assert (got.sim_lead_mean_days.astype(float) - exp["mean"]).abs().max() < 0.01
    assert (got.sim_lead_p90_days.astype(float) - exp.p90).abs().max() < 1e-6
    std = got.sim_lead_stddev_days.astype(float).dropna()
    assert (std - exp["std"][std.index]).abs().max() < 0.01


def test_supplier_rank_only_for_suppliers_with_enough_pos(marts, engine):
    sc = q(engine, "SELECT * FROM marts.supplier_scorecard")
    ranked = sc[sc.reliability_rank.notna()]
    assert (ranked.sim_po_count >= 5).all()
    assert sc[sc.sim_po_count.fillna(0) < 5].reliability_rank.isna().all()
    top = ranked.sort_values("reliability_rank").iloc[0]
    assert float(top.sim_po_on_time_pct) == float(ranked.sim_po_on_time_pct.astype(float).max())


def test_inventory_turnover_and_days_of_supply_match_pandas(marts, engine):
    snap = q(engine, "SELECT d.date AS week, i.* FROM core.fact_inventory_snapshot i "
                     "JOIN core.dim_date d ON d.date_key = i.snapshot_date_key")
    snap["week"] = pd.to_datetime(snap.week)
    snap["unit_cost"] = snap.unit_cost.astype(float)
    last = snap.week.max()
    cur = q(engine, "SELECT * FROM marts.inventory_position_current")
    for _, row in cur.sample(min(15, len(cur)), random_state=1).iterrows():
        s = snap[(snap.product_key == row.product_key) & (snap.warehouse_key == row.warehouse_key)]
        t52 = s[s.week > last - pd.Timedelta(days=364)]
        cogs = (t52.units_sold * t52.unit_cost).sum()
        avg_inv = (t52.on_hand * t52.unit_cost).mean()
        if avg_inv > 0:
            assert float(row.inventory_turnover_52w) == pytest.approx(cogs / avg_inv, abs=0.01)
        d13 = s[s.week > last - pd.Timedelta(days=91)].units_demand.sum()
        latest_on_hand = s[s.week == last].on_hand.iloc[0]
        if d13 > 0:
            assert float(row.days_of_supply) == pytest.approx(latest_on_hand / (d13 / 91), abs=0.06)
        else:
            assert pd.isna(row.days_of_supply)


def test_risk_flags_follow_definitions(marts, engine):
    cur = q(engine, "SELECT * FROM marts.inventory_position_current")
    assert (cur[cur.is_overstock].days_of_supply.astype(float) > 120).all()
    risk = cur[cur.is_stockout_risk]
    assert (risk.days_of_supply.astype(float) < risk.supplier_lead_p90_days.astype(float)).all()
    assert (risk.on_hand > 0).all()
    assert (cur[cur.on_hand == 0].risk_status == "stockout").all()
    assert set(cur.risk_status) <= {"stockout", "no_recent_demand", "overstock", "at_risk", "healthy"}


def test_stockout_weeks_match_snapshot_counts(marts, engine):
    a = scalar(engine, "SELECT sum(stockout_weeks) FROM marts.inventory_position_current")
    b = scalar(engine, "SELECT count(*) FROM core.fact_inventory_snapshot WHERE on_hand = 0")
    assert a == b


def test_review_buckets_are_ordered_and_populated(marts, engine):
    r = q(engine, "SELECT sort_order, bucket, orders FROM marts.review_vs_lateness ORDER BY sort_order")
    assert r.sort_order.tolist() == sorted(r.sort_order)
    assert r.orders.sum() > 0


def test_marts_dq_checks_pass(marts, engine):
    res = dq.run_checks(["marts"], raise_on_critical=False)
    assert len(res) == 8
    assert all(r.passed for r in res), dq.summarize(res)


def test_marts_idempotent(marts, engine):
    before = scalar(engine, "SELECT count(*) FROM marts.inventory_position_current")
    db.run_sql_dir("40_marts")
    assert scalar(engine, "SELECT count(*) FROM marts.inventory_position_current") == before
