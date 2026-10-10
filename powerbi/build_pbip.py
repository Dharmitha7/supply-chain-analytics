"""Generate the Power BI project (.pbip: TMDL semantic model + PBIR report) from the live marts catalog.

    python powerbi/build_pbip.py

Output (committed, diff-friendly text): powerbi/SupplyChain.pbip, SupplyChain.SemanticModel/, SupplyChain.Report/
Regenerate after changing marts columns. Open SupplyChain.pbip in Power BI Desktop (free).
"""
import json
import uuid
from pathlib import Path

import pandas as pd

from sca import db

OUT = Path(__file__).resolve().parent
SM = OUT / "SupplyChain.SemanticModel"
RP = OUT / "SupplyChain.Report"

TABLES = ["shipments", "inventory_snapshot", "dim_date", "dim_product", "dim_supplier", "dim_warehouse",
          "delivery_by_state", "review_vs_lateness", "supplier_scorecard", "supplier_ranking", "inventory_position_current",
          "kpi_delivery_monthly", "dq_latest", "quarantine_summary"]

TYPE = {"integer": "int64", "bigint": "int64", "smallint": "int64", "numeric": "double", "double precision": "double",
        "real": "double", "boolean": "boolean", "text": "string", "date": "dateTime",
        "timestamp without time zone": "dateTime", "timestamp with time zone": "dateTime"}

# ---------------------------------------------------------------- DAX measures: (table, name, expression, format)
MEASURES = [
    ("shipments", "Orders", "COUNTROWS(shipments)", "#,0"),
    ("shipments", "Delivered Orders", "CALCULATE(COUNTROWS(shipments), shipments[is_delivered] = TRUE()) + 0", "#,0"),
    ("shipments", "OTD Eligible Orders", "CALCULATE(COUNTROWS(shipments), NOT ISBLANK(shipments[is_on_time]))", "#,0"),
    ("shipments", "On-time Delivery %",
     "DIVIDE(CALCULATE(COUNTROWS(shipments), shipments[is_on_time] = TRUE()), [OTD Eligible Orders])", "0.0%"),
    ("shipments", "Avg Days Late", "AVERAGE(shipments[days_late])", "0.00"),
    ("shipments", "Avg Cycle Days", "CALCULATE(AVERAGE(shipments[cycle_days]), shipments[is_delivered] = TRUE())", "0.0"),
    ("shipments", "Avg Carrier Handoff Days", "AVERAGE(shipments[carrier_handoff_days])", "0.00"),
    ("shipments", "Fulfillment Rate",
     "DIVIDE([Delivered Orders], CALCULATE(COUNTROWS(shipments), shipments[is_canceled] = FALSE()))", "0.0%"),
    ("shipments", "Cancel Rate",
     "DIVIDE(CALCULATE(COUNTROWS(shipments), shipments[is_canceled] = TRUE()), [Orders])", "0.0%"),
    ("shipments", "Avg Review Score", "AVERAGE(shipments[review_score])", "0.00"),
    ("inventory_snapshot", "Stockout Rate (sim)",
     "DIVIDE(CALCULATE(COUNTROWS(inventory_snapshot), inventory_snapshot[is_stockout] = TRUE()), COUNTROWS(inventory_snapshot))", "0.0%"),
    ("inventory_snapshot", "Fill Rate (sim)",
     "DIVIDE(SUM(inventory_snapshot[units_sold]), SUM(inventory_snapshot[units_demand]))", "0.0%"),
    ("inventory_snapshot", "Avg Inventory Value (sim)",
     "DIVIDE(SUM(inventory_snapshot[inventory_value]), DISTINCTCOUNT(inventory_snapshot[snapshot_date_key]))", "#,0"),
    ("inventory_snapshot", "Inventory Turnover (sim, annualized)",
     "DIVIDE(SUM(inventory_snapshot[cogs]), [Avg Inventory Value (sim)]) * 52 / DISTINCTCOUNT(inventory_snapshot[snapshot_date_key])", "0.00"),
    ("inventory_position_current", "SKU-DC Series", "COUNTROWS(inventory_position_current)", "#,0"),
    ("inventory_position_current", "Stocked Out Now (sim)",
     "CALCULATE(COUNTROWS(inventory_position_current), inventory_position_current[risk_status] = \"stockout\") + 0", "#,0"),
    ("inventory_position_current", "At Risk (sim)",
     "CALCULATE(COUNTROWS(inventory_position_current), inventory_position_current[is_stockout_risk] = TRUE()) + 0", "#,0"),
    ("inventory_position_current", "Overstock (sim)",
     "CALCULATE(COUNTROWS(inventory_position_current), inventory_position_current[is_overstock] = TRUE()) + 0", "#,0"),
    ("dq_latest", "DQ Checks", "COUNTROWS(dq_latest)", "#,0"),
    ("dq_latest", "DQ Critical Failures", "CALCULATE(COUNTROWS(dq_latest), dq_latest[status] = \"FAIL\") + 0", "#,0"),
    ("dq_latest", "DQ Warnings", "CALCULATE(COUNTROWS(dq_latest), dq_latest[status] = \"WARN\") + 0", "#,0"),
]

SORT_BY = {("review_vs_lateness", "bucket"): "sort_order"}

RELATIONSHIPS = [  # (from table.col [many], to table.col [one])
    ("shipments.purchase_date_key", "dim_date.date_key"),
    ("shipments.warehouse_key", "dim_warehouse.warehouse_key"),
    ("inventory_snapshot.snapshot_date_key", "dim_date.date_key"),
    ("inventory_snapshot.product_key", "dim_product.product_key"),
    ("inventory_snapshot.warehouse_key", "dim_warehouse.warehouse_key"),
    ("inventory_snapshot.supplier_key", "dim_supplier.supplier_key"),
    ("supplier_scorecard.supplier_key", "dim_supplier.supplier_key"),
    ("supplier_ranking.supplier_key", "dim_supplier.supplier_key"),
    ("inventory_position_current.product_key", "dim_product.product_key"),
    ("inventory_position_current.warehouse_key", "dim_warehouse.warehouse_key"),
]


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def q(name: str) -> str:
    return name if name.replace("_", "").isalnum() else f"'{name}'"


def semantic_model() -> None:
    cols = pd.read_sql("""SELECT table_name, column_name, data_type FROM information_schema.columns
                          WHERE table_schema = 'marts' ORDER BY table_name, ordinal_position""", db.engine())
    w(SM / "definition.pbism", json.dumps({"version": "4.0", "settings": {}}, indent=2))
    w(SM / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    w(SM / "definition" / "model.tmdl",
      "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\n"
      + "".join(f"ref table {t}\n" for t in TABLES) + "\nref expression Server\nref expression Database\n")
    w(SM / "definition" / "expressions.tmdl",
      'expression Server = "localhost:5432" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
      '\tlineageTag: ' + str(uuid.uuid5(uuid.NAMESPACE_DNS, "sca.server")) + '\n\n'
      'expression Database = "supply_chain" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
      '\tlineageTag: ' + str(uuid.uuid5(uuid.NAMESPACE_DNS, "sca.database")) + '\n')
    for t in TABLES:
        c = cols[cols.table_name == t]
        if c.empty:
            raise SystemExit(f"marts.{t} not found - run the pipeline first")
        lines = [f"table {q(t)}", f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_DNS, 'sca.t.' + t)}", ""]
        for _, name, expr, fmt in [m for m in MEASURES if m[0] == t]:
            lines += [f"\tmeasure '{name}' = {expr}", f"\t\tformatString: {fmt}", ""]
        for r in c.itertuples():
            dt = TYPE.get(r.data_type, "string")
            lines += [f"\tcolumn {q(r.column_name)}", f"\t\tdataType: {dt}",
                      f"\t\tsourceColumn: {r.column_name}",
                      f"\t\tsummarizeBy: {'none' if dt in ('string', 'boolean', 'dateTime') or r.column_name.endswith('_key') else 'sum'}"]
            if (t, r.column_name) in SORT_BY:
                lines.append(f"		sortByColumn: {SORT_BY[(t, r.column_name)]}")
            if dt == "dateTime":
                lines.append("\t\tformatString: yyyy-mm-dd")
            lines.append("")
        lines += [f"\tpartition {q(t)} = m", "\t\tmode: import", "\t\tsource =",
                  "\t\t\tlet", "\t\t\t\tSource = PostgreSQL.Database(Server, Database),",
                  f'\t\t\t\tData = Source{{[Schema="marts",Item="{t}"]}}[Data]', "\t\t\tin", "\t\t\t\tData", ""]
        w(SM / "definition" / "tables" / f"{t}.tmdl", "\n".join(lines))
    rel = []
    for i, (f, t) in enumerate(RELATIONSHIPS):
        rel += [f"relationship {uuid.uuid5(uuid.NAMESPACE_DNS, f'sca.rel.{i}')}", f"\tfromColumn: {f}", f"\ttoColumn: {t}", ""]
    w(SM / "definition" / "relationships.tmdl", "\n".join(rel))


# ---------------------------------------------------------------- report (PBIR)
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"


def col(t, c):
    return {"Column": {"Expression": {"SourceRef": {"Entity": t}}, "Property": c}}


def meas(t, m):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": t}}, "Property": m}}


def agg(t, c, fn):  # 0 sum, 1 avg, 2 min, 3 max, 5 count
    return {"Aggregation": {"Expression": col(t, c), "Function": fn}}


def proj(field, ref, active=False):
    p = {"field": field, "queryRef": ref}
    if active:
        p["active"] = True
    return p


def M(t, m):
    return proj(meas(t, m), f"{t}.{m}")


def C(t, c):
    return proj(col(t, c), f"{t}.{c}", True)


def A(t, c, fn, label):
    return proj(agg(t, c, fn), f"{label}({t}.{c})")


def visual(vtype, title, x, y, wd, ht, roles, z=0, sort=None, labels=False, dropdown=False):
    v = {
        "$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": uuid.uuid4().hex[:20],
        "position": {"x": x, "y": y, "z": z, "width": wd, "height": ht},
        "visual": {"visualType": vtype,
                   "query": {"queryState": {r: {"projections": p} for r, p in roles.items()}},
                   "visualContainerObjects": {"title": [{"properties": {
                       "text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]},
                   "drillFilterOtherVisuals": True}}
    if sort:   # (field, "Ascending" | "Descending")
        v["visual"]["query"]["sortDefinition"] = {"sort": [{"field": sort[0], "direction": sort[1]}], "isDefaultSort": True}
    objs = {}
    if vtype in ("lineChart", "clusteredBarChart", "clusteredColumnChart"):   # titles already name the measure
        off = [{"properties": {"showAxisTitle": {"expr": {"Literal": {"Value": "false"}}}}}]
        objs["categoryAxis"] = off
        objs["valueAxis"] = off
    if labels:
        objs["labels"] = [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]
    if dropdown:
        objs["data"] = [{"properties": {"mode": {"expr": {"Literal": {"Value": "'Dropdown'"}}}}}]
    if objs:
        v["visual"]["objects"] = objs
    return v


def card(title, t, m, x, y):
    return visual("card", title, x, y, 232, 110, {"Values": [M(t, m)]})


PAGES = {
    "Executive Overview": [
        card("On-time delivery", "shipments", "On-time Delivery %", 20, 20),
        card("Fulfillment rate", "shipments", "Fulfillment Rate", 262, 20),
        card("Avg review score", "shipments", "Avg Review Score", 504, 20),
        card("Inventory turnover (simulated)", "inventory_snapshot", "Inventory Turnover (sim, annualized)", 746, 20),
        card("Weekly stockout rate (simulated)", "inventory_snapshot", "Stockout Rate (sim)", 988, 20),
        visual("lineChart", "On-time delivery % by month (real)", 20, 150, 620, 270,
               {"Category": [C("dim_date", "year_month")], "Y": [M("shipments", "On-time Delivery %")]}),
        visual("lineChart", "Weekly stockout rate by month (simulated)", 660, 150, 600, 270,
               {"Category": [C("dim_date", "year_month")], "Y": [M("inventory_snapshot", "Stockout Rate (sim)")]}),
        visual("lineChart", "Orders by month (real)", 20, 440, 620, 250,
               {"Category": [C("dim_date", "year_month")], "Y": [M("shipments", "Orders")]}),
        visual("lineChart", "Fill rate by month (simulated)", 660, 440, 600, 250,
               {"Category": [C("dim_date", "year_month")], "Y": [M("inventory_snapshot", "Fill Rate (sim)")]}),
    ],
    "Delivery Performance": [
        card("On-time delivery", "shipments", "On-time Delivery %", 20, 20),
        card("Avg days late", "shipments", "Avg Days Late", 262, 20),
        card("Avg cycle days", "shipments", "Avg Cycle Days", 504, 20),
        card("Carrier handoff days", "shipments", "Avg Carrier Handoff Days", 746, 20),
        card("Cancel rate", "shipments", "Cancel Rate", 988, 20),
        visual("clusteredBarChart", "On-time % by customer state (lowest first)", 20, 150, 620, 540,
               {"Category": [C("delivery_by_state", "state")], "Y": [A("delivery_by_state", "otd_pct", 1, "Avg")]},
               sort=(agg("delivery_by_state", "otd_pct", 1), "Ascending"), labels=True),
        visual("clusteredColumnChart", "Review score vs lateness", 660, 150, 600, 260,
               {"Category": [C("review_vs_lateness", "bucket")], "Y": [A("review_vs_lateness", "avg_review_score", 1, "Avg")]}, labels=True),
        visual("lineChart", "Avg cycle days by month", 660, 430, 600, 260,
               {"Category": [C("dim_date", "year_month")], "Y": [M("shipments", "Avg Cycle Days")]}),
    ],
    "Supplier Scorecard": [
        visual("tableEx", "Supplier ranking, best first (delivery = real; sim_* = simulated lead times)", 20, 20, 1240, 360,
               {"Values": [C("supplier_ranking", "reliability_rank"), C("supplier_ranking", "seller_id"),
                           C("supplier_ranking", "orders"), C("supplier_ranking", "delivery_otd_pct"),
                           C("supplier_ranking", "sim_lead_mean_days"), C("supplier_ranking", "sim_lead_p90_days"),
                           C("supplier_ranking", "sim_lead_stddev_days"), C("supplier_ranking", "sim_po_on_time_pct")]},
               sort=(col("supplier_ranking", "reliability_rank"), "Ascending")),
        visual("scatterChart", "Lead-time variability (y, std dev days) vs mean lead time (x, days), bubble = orders (simulated)", 20, 400, 700, 300,
               {"Category": [C("supplier_scorecard", "seller_id")],
                "X": [A("supplier_scorecard", "sim_lead_mean_days", 1, "Avg")],
                "Y": [A("supplier_scorecard", "sim_lead_stddev_days", 1, "Avg")],
                "Size": [A("supplier_scorecard", "orders", 0, "Sum")]}),
        visual("slicer", "Supplier state", 740, 400, 520, 90, {"Values": [C("dim_supplier", "state")]}, dropdown=True),
    ],
    "Inventory Risk & DQ": [
        card("Stocked out now (simulated)", "inventory_position_current", "Stocked Out Now (sim)", 20, 20),
        card("At risk (simulated)", "inventory_position_current", "At Risk (sim)", 262, 20),
        card("Overstock >120d (simulated)", "inventory_position_current", "Overstock (sim)", 504, 20),
        card("DQ critical failures", "dq_latest", "DQ Critical Failures", 746, 20),
        card("DQ warnings", "dq_latest", "DQ Warnings", 988, 20),
        visual("clusteredColumnChart", "SKU-DC series by risk status (simulated)", 20, 150, 400, 270,
               {"Category": [C("inventory_position_current", "risk_status")],
                "Y": [M("inventory_position_current", "SKU-DC Series")]},
               sort=(meas("inventory_position_current", "SKU-DC Series"), "Descending"), labels=True),
        visual("slicer", "Filter positions by risk status", 20, 440, 400, 250, {"Values": [C("inventory_position_current", "risk_status")]}),
        visual("tableEx", "Inventory positions, most stockout weeks first (simulated)", 440, 150, 820, 270,
               {"Values": [C("dim_product", "category_en"), C("dim_warehouse", "warehouse_code"),
                           C("inventory_position_current", "risk_status"), C("inventory_position_current", "on_hand"),
                           C("inventory_position_current", "days_of_supply"),
                           C("inventory_position_current", "supplier_lead_p90_days"),
                           C("inventory_position_current", "inventory_turnover_52w"),
                           C("inventory_position_current", "stockout_week_pct")]},
               sort=(col("inventory_position_current", "stockout_week_pct"), "Descending")),
        visual("tableEx", "Data-quality checks, latest run (failures and warnings first)", 440, 440, 820, 250,
               {"Values": [C("dq_latest", "status"), C("dq_latest", "layer"), C("dq_latest", "check_id"),
                           C("dq_latest", "description"), C("dq_latest", "value"), C("dq_latest", "threshold")]},
               sort=(col("dq_latest", "status_order"), "Ascending")),
    ],
}


def report() -> None:
    w(OUT / "SupplyChain.pbip", json.dumps({
        "version": "1.0", "artifacts": [{"report": {"path": "SupplyChain.Report"}}], "settings": {"enableAutoRecovery": True}}, indent=2))
    w(RP / "definition.pbir", json.dumps({
        "version": "4.0", "datasetReference": {"byPath": {"path": "../SupplyChain.SemanticModel"}}}, indent=2))
    d = RP / "definition"
    w(d / "version.json", json.dumps({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2))
    w(d / "report.json", json.dumps({"$schema": f"{SCHEMA}/report/2.0.0/schema.json",
                                     "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61",
                                                                       "type": "SharedResources"}}}, indent=2))
    ids = []
    for i, (name, visuals) in enumerate(PAGES.items()):
        pid = f"page{i + 1:02d}"
        ids.append(pid)
        w(d / "pages" / pid / "page.json", json.dumps({
            "$schema": f"{SCHEMA}/page/1.0.0/schema.json", "name": pid, "displayName": name,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2))
        for j, v in enumerate(visuals):
            v["name"] = f"{pid}v{j + 1:02d}"
            w(d / "pages" / pid / "visuals" / v["name"] / "visual.json", json.dumps(v, indent=2))
    w(d / "pages" / "pages.json", json.dumps({
        "$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json", "pageOrder": ids, "activePageName": ids[0]}, indent=2))


def theme() -> None:
    """Theme built from the validated reference palette (light mode); import via View > Themes > Browse."""
    series = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
    w(OUT / "theme.json", json.dumps({
        "name": "SupplyChain", "dataColors": series, "background": "#fcfcfb", "foreground": "#0b0b0b",
        "tableAccent": series[0], "good": "#0ca30c", "neutral": "#fab219", "bad": "#d03b3b",
        "textClasses": {"title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#0b0b0b"},
                        "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#52514e"}},
        "visualStyles": {"*": {"*": {"grid": [{"gridlineColor": {"solid": {"color": "#e1e0d9"}}}],
                                     "background": [{"color": {"solid": {"color": "#fcfcfb"}}, "transparency": 0}]}}}}, indent=2))


if __name__ == "__main__":
    semantic_model()
    report()
    theme()
    print(f"wrote Power BI project to {OUT}")
