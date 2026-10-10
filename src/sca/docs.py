"""Generate docs/data_dictionary.md from the live database catalog (types) plus curated descriptions.

    python -m sca.docs
"""
import pandas as pd

from . import db
from .config import ROOT

TABLES = {
    "core": {
        "dim_date": "Calendar, one row per day. `date_key` = yyyymmdd.",
        "dim_product": "Products with English category and physical attributes.",
        "dim_supplier": "Suppliers = Olist sellers, with city/state/lat/lng.",
        "dim_customer": "Customers (per order-level customer_id) with state, coordinates, and their regional DC (SIMULATED mapping).",
        "dim_warehouse": "4 regional DCs. **Simulated**: Olist has no warehouse concept.",
        "map_state_warehouse": "Customer state -> nearest regional DC. **Simulated** assignment.",
        "fact_order_line": "One row per order line (1 unit each). Real.",
        "fact_shipment": "One row per order with derived delivery timings and on-time flag. Real.",
        "fact_purchase_order": "Replenishment POs. **Simulated.**",
        "fact_inventory_snapshot": "Weekly end-of-week stock per product x DC. **Simulated** (demand inside it is real).",
    },
    "marts": {
        "kpi_delivery_monthly": "Delivery KPIs by order-purchase month. Real.",
        "delivery_by_state": "Delivery KPIs by customer state. Real.",
        "review_vs_lateness": "Average review score by lateness bucket. Real.",
        "supplier_lead_time": "Lead-time mean/p90/stddev per supplier over received POs. **Simulated.**",
        "supplier_scorecard": "One row per supplier: real delivery outcomes (`delivery_*`) plus simulated lead-time stats (`sim_*`) and rank.",
        "inventory_position_current": "Latest-week position per product x DC with days of supply, turnover and risk status. **Simulated.**",
        "inventory_weekly": "Weekly totals: stockout rate, fill rate, inventory value, COGS. **Simulated.**",
        "kpi_executive_monthly": "Monthly delivery (real) and inventory (`sim_*`) KPIs side by side.",
        "shipments": "Row-level shipments for Power BI (DAX measures).",
        "inventory_snapshot": "Row-level inventory snapshots for Power BI.",
        "quarantine_summary": "Rows rejected by cleaning, by source table and reason code.",
        "dq_latest": "Results of the most recent data-quality run.",
        "dim_date": "Pass-through of core.dim_date.", "dim_product": "Pass-through of core.dim_product.",
        "dim_supplier": "Pass-through of core.dim_supplier.", "dim_warehouse": "Pass-through of core.dim_warehouse.",
    },
    "sim": {
        "supplier_profile": "Per-supplier simulation parameters: quoted vs actual lead time, sigma, reliability flag.",
        "inventory_weekly": "Raw simulator output (natural surrogate keys).",
        "purchase_order": "Raw simulator PO output.",
    },
    "dq": {
        "check": "Check definitions (id, layer, severity, SQL, threshold). Reloaded from sql/90_dq on each run.",
        "result": "Append-only check results per run_id.",
        "run_log": "Row counts in/out for each pipeline step.",
    },
}

NOTES = {
    "is_on_time": "delivered date <= estimated date; NULL if not delivered/no dates",
    "days_late": "max(delivered date - estimated date, 0)",
    "days_vs_estimate": "delivered date - estimated date; negative = early",
    "cycle_days": "purchase -> delivered, days",
    "carrier_handoff_days": "approved -> carrier pickup, days",
    "ship_lead_days": "purchase -> carrier pickup, days",
    "is_delivered": "status = delivered AND delivery date present",
    "is_canceled": "status canceled or unavailable",
    "is_simulated": "TRUE = synthetic row (never real Olist data)",
    "is_stockout": "on_hand = 0 at week end",
    "days_of_supply": "on_hand / trailing-13-week average daily demand; NULL when no demand",
    "inventory_turnover_52w": "COGS / average inventory value over trailing 52 weeks",
    "risk_status": "stockout | no_recent_demand | overstock (>120d) | at_risk (days of supply < supplier p90 lead) | healthy",
    "is_stockout_risk": "on_hand > 0 and days_of_supply < supplier p90 lead time",
    "is_overstock": "days_of_supply > 120",
    "reliability_rank": "1 = best; only suppliers with >= 5 received POs",
    "sim_lead_p90_days": "90th percentile PO lead time (days), received POs only",
    "delivery_otd_pct": "real on-time % for orders this seller shipped",
    "unit_cost": "SIMULATED proxy: 60% of mean selling price",
    "lead_days": "days from PO to receipt",
    "expected_date": "order date + supplier's quoted lead time",
    "is_received": "receipt date is within simulated horizon",
    "qty": "units (Olist: 1 per order line)",
    "date_key": "yyyymmdd integer",
    "warehouse_key": "regional DC (simulated mapping)",
    "threshold": "check passes when value <= threshold",
    "severity": "critical stops the pipeline; warn is reported only",
}


def build() -> str:
    cols = pd.read_sql("""SELECT table_schema, table_name, column_name, data_type, ordinal_position
                          FROM information_schema.columns WHERE table_schema IN ('core','marts','sim','dq')
                          ORDER BY 1, 2, 5""", db.engine())
    out = ["# Data dictionary", "",
           "_Generated by `python -m sca.docs` from the database catalog. **Simulated** objects are labelled; "
           "everything else derives from real Olist data._", "",
           "Layers: `raw` (as-loaded text) → `staging` (typed, cleaned; rejects in `staging.quarantine`) → `core` (star schema) "
           "→ `marts` (Power BI views). `sim` and `dq` are side schemas.", ""]
    for schema, tables in TABLES.items():
        out += [f"## `{schema}`", ""]
        for t, desc in tables.items():
            c = cols[(cols.table_schema == schema) & (cols.table_name == t)]
            if c.empty:
                continue
            out += [f"### {schema}.{t}", "", desc, "", "| Column | Type | Notes |", "|---|---|---|"]
            out += [f"| `{r.column_name}` | {r.data_type} | {NOTES.get(r.column_name, '')} |" for r in c.itertuples()]
            out.append("")
    q = pd.read_sql("SELECT DISTINCT reason_code FROM staging.quarantine ORDER BY 1", db.engine())
    out += ["## Quarantine reason codes", "",
            "| Code | Meaning |", "|---|---|",
            "| MISSING_KEY | Primary key blank |", "| DUPLICATE_KEY | Duplicate of an earlier row with the same key |",
            "| MISSING_PURCHASE_DATE | Order without purchase timestamp |", "| ORPHAN_CUSTOMER / ORPHAN_ORDER / ORPHAN_PRODUCT / ORPHAN_SELLER | Foreign key has no parent after cleaning |",
            "| DELIVERED_BEFORE_PURCHASE | Delivery timestamp earlier than purchase |",
            "| CANCELED_WITH_DELIVERY_DATE | Canceled/unavailable order carrying a delivery date |",
            "| INVALID_PRICE / INVALID_FREIGHT / INVALID_PAYMENT | price <= 0, freight < 0, payment < 0 |",
            "| INVALID_SCORE | Review score outside 1-5 |", "| SUPERSEDED_DUPLICATE | An older review for an order that has a newer one |", "",
            f"Codes observed in the current run: {', '.join(q.reason_code) or 'none'}.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    path = ROOT / "docs" / "data_dictionary.md"
    path.write_text(build(), encoding="utf-8")
    print(f"wrote {path}")
