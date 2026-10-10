import numpy as np
import pytest

from sca import db, simulate
from sca.simulate import simulate_series

from .conftest import scalar


# ---------- pure policy mechanics ----------
def test_stock_is_conserved():
    d = np.array([3, 0, 5, 2, 0, 4, 1, 6, 0, 2] * 5)
    r = simulate_series(d, reorder_point=6, order_up_to=15, draw_lead_days=lambda: 9)
    assert 15 + r.received.sum() - r.sold.sum() == r.on_hand[-1]
    assert (r.on_hand >= 0).all() and (r.sold <= d).all()


def test_reliable_short_lead_has_no_stockouts_and_slow_long_lead_does():
    d = np.full(40, 4)
    ok = simulate_series(d, reorder_point=12, order_up_to=30, draw_lead_days=lambda: 3)
    bad = simulate_series(d, reorder_point=12, order_up_to=30, draw_lead_days=lambda: 35)
    assert (ok.on_hand == 0).sum() == 0
    assert (bad.on_hand == 0).sum() > 0


def test_no_reorder_while_position_above_reorder_point():
    d = np.zeros(10, dtype=int)
    r = simulate_series(d, reorder_point=5, order_up_to=20, draw_lead_days=lambda: 7)
    assert r.pos == []


def test_lead_days_map_to_arrival_week():
    d = np.array([20] + [0] * 6)     # week 0 sells everything -> order at end of week 0
    r = simulate_series(d, reorder_point=2, order_up_to=20, draw_lead_days=lambda: 8)
    assert r.received.tolist()[:4] == [0, 0, 20, 0]    # 8 days from end of wk0 -> lands in week 2


# ---------- built from the sample DB ----------
@pytest.fixture(scope="session")
def simulated(staged):
    db.run_sql_dir("20_core_dims")
    db.run_sql_dir("30_core_facts")
    simulate.run()
    db.run_sql_dir("35_core_sim_facts")
    return True


def test_simulation_is_deterministic(simulated, engine):
    first = scalar(engine, "SELECT sum(on_hand * 31 + on_order) FROM core.fact_inventory_snapshot")
    simulate.run()
    db.run_sql_dir("35_core_sim_facts")
    assert scalar(engine, "SELECT sum(on_hand * 31 + on_order) FROM core.fact_inventory_snapshot") == first


def test_every_sim_row_is_flagged_simulated(simulated, engine):
    for t in ("fact_purchase_order", "fact_inventory_snapshot"):
        assert scalar(engine, f"SELECT count(*) FROM core.{t} WHERE NOT is_simulated") == 0
    assert scalar(engine, "SELECT count(*) FROM core.dim_warehouse WHERE NOT is_simulated") == 0


def test_simulated_demand_equals_real_sales(simulated, engine):
    """Within the simulated week range and product set, demand is exactly the real order-line count."""
    sim = scalar(engine, "SELECT sum(units_demand) FROM core.fact_inventory_snapshot")
    real = scalar(engine, """SELECT count(*) FROM core.fact_order_line f JOIN core.dim_date d ON d.date_key = f.purchase_date_key
                             WHERE f.order_status NOT IN ('canceled','unavailable')
                             AND d.week_start BETWEEN (SELECT min(d2.date) FROM core.fact_inventory_snapshot i JOIN core.dim_date d2 ON d2.date_key = i.snapshot_date_key)
                                                  AND (SELECT max(d2.date) FROM core.fact_inventory_snapshot i JOIN core.dim_date d2 ON d2.date_key = i.snapshot_date_key)
                             AND f.product_key IN (SELECT DISTINCT product_key FROM core.fact_inventory_snapshot)""")
    assert sim == real


def test_receipts_never_exceed_purchase_orders(simulated, engine):
    receipts = scalar(engine, "SELECT sum(units_received) FROM core.fact_inventory_snapshot")
    assert 0 < receipts <= scalar(engine, "SELECT sum(qty) FROM core.fact_purchase_order WHERE is_received")


def test_unreliable_suppliers_are_slower(simulated, engine):
    rel = scalar(engine, """SELECT avg(po.lead_days::numeric / sp.quoted_lead_days)
                            FROM core.fact_purchase_order po JOIN sim.supplier_profile sp USING (supplier_key)
                            WHERE NOT sp.is_unreliable""")
    unrel = scalar(engine, """SELECT avg(po.lead_days::numeric / sp.quoted_lead_days)
                              FROM core.fact_purchase_order po JOIN sim.supplier_profile sp USING (supplier_key)
                              WHERE sp.is_unreliable""")
    if unrel is not None:      # sample has only 10 suppliers; there may be none unreliable
        assert unrel > rel


def test_stockouts_and_positive_stock_both_occur(simulated, engine):
    assert scalar(engine, "SELECT count(*) FROM core.fact_inventory_snapshot WHERE is_stockout") > 0
    assert scalar(engine, "SELECT count(*) FROM core.fact_inventory_snapshot WHERE NOT is_stockout") > 0
