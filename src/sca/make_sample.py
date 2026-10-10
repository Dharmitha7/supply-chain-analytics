"""Generate a small, deterministic, SYNTHETIC Olist-shaped dataset for CI and tests.

Olist data cannot be redistributed, so the committed sample is fabricated (seeded numpy).
It deliberately contains a fixed set of dirty rows so tests can prove each cleaning rule fires.
Expected outcomes are in EXPECTED_QUARANTINE / EXPECTED_FLAGS below.

    python -m sca.make_sample [outdir]      # default: data/sample
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT

SEED = 42
STATES = ["SP", "RJ", "MG", "RS", "PR", "BA", "SC", "PE"]
CATEGORIES = {  # pt -> en
    "beleza_saude": "health_beauty", "informatica_acessorios": "computers_accessories",
    "esporte_lazer": "sports_leisure", "moveis_decoracao": "furniture_decor",
    "brinquedos": "toys", "cama_mesa_banho": "bed_bath_table",
}
N_CUSTOMERS, N_SELLERS, N_PRODUCTS, N_ORDERS = 300, 10, 40, 600
FMT = "%Y-%m-%d %H:%M:%S"

# (source_table, reason_code) -> rows the staging layer must quarantine for the committed sample
EXPECTED_QUARANTINE = {
    ("orders", "CANCELED_WITH_DELIVERY_DATE"): 2,
    ("orders", "DELIVERED_BEFORE_PURCHASE"): 1,
    ("order_items", "DUPLICATE_KEY"): 3,
    ("order_items", "INVALID_PRICE"): 2,
    ("order_items", "INVALID_FREIGHT"): 1,
    ("order_items", "ORPHAN_ORDER"): 5,      # 2 injected + the single item of each of the 3 quarantined orders
    ("order_reviews", "ORPHAN_ORDER"): 3,
    ("order_reviews", "SUPERSEDED_DUPLICATE"): 4,
    ("order_payments", "ORPHAN_ORDER"): 3,
}
# soft anomalies: kept in staging.orders but flagged
EXPECTED_FLAGS = {
    "flag_delivered_missing_date": 2,
    "flag_approved_before_purchase": 1,
    "flag_carrier_after_delivery": 1,
}


def _ts(s: pd.Series) -> pd.Series:
    return s.dt.strftime(FMT).where(s.notna(), "")


def build(outdir: Path) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    cust = pd.DataFrame({
        "customer_id": [f"c{i:05d}" for i in range(N_CUSTOMERS)],
        "customer_unique_id": [f"u{i:05d}" for i in range(N_CUSTOMERS)],
        "customer_zip_code_prefix": [f"{rng.integers(1000, 99999):05d}" for _ in range(N_CUSTOMERS)],
        "customer_city": "sample city",
        "customer_state": rng.choice(STATES, N_CUSTOMERS),
    })
    sellers = pd.DataFrame({
        "seller_id": [f"s{i:03d}" for i in range(N_SELLERS)],
        "seller_zip_code_prefix": [f"{rng.integers(1000, 99999):05d}" for _ in range(N_SELLERS)],
        "seller_city": "sample city", "seller_state": rng.choice(STATES, N_SELLERS),
    })
    cats = list(CATEGORIES)
    products = pd.DataFrame({
        "product_id": [f"p{i:04d}" for i in range(N_PRODUCTS)],
        "product_category_name": rng.choice(cats, N_PRODUCTS),
        "product_name_lenght": 40, "product_description_lenght": 300, "product_photos_qty": 2,
        "product_weight_g": rng.integers(100, 5000, N_PRODUCTS).astype(str),
        "product_length_cm": 20, "product_height_cm": 10, "product_width_cm": 15,
    })
    products.loc[0, "product_category_name"] = ""        # missing category -> 'unknown'
    products.loc[1, "product_weight_g"] = "0"            # invalid measure -> NULL
    translation = pd.DataFrame({"product_category_name": cats,
                                "product_category_name_english": [CATEGORIES[c] for c in cats]})

    # ---- orders ----
    start = pd.Timestamp("2017-01-01")
    purchase = start + pd.to_timedelta(rng.integers(0, 545 * 24 * 3600, N_ORDERS), unit="s")
    approved = purchase + pd.to_timedelta(rng.integers(600, 40000, N_ORDERS), unit="s")
    carrier = approved + pd.to_timedelta(rng.integers(1, 4, N_ORDERS), unit="D")
    # seller-independent order-level delivery time; ~15% late vs estimate
    transit = pd.to_timedelta((rng.lognormal(2.0, 0.5, N_ORDERS) * 86400).astype(int), unit="s")
    delivered = carrier + transit
    estimated = purchase + pd.to_timedelta(rng.integers(14, 30, N_ORDERS), unit="D")
    status = np.where(rng.random(N_ORDERS) < 0.04, "canceled", "delivered")
    orders = pd.DataFrame({
        "order_id": [f"o{i:05d}" for i in range(N_ORDERS)],
        "customer_id": rng.choice(cust.customer_id, N_ORDERS),
        "order_status": status,
        "order_purchase_timestamp": purchase, "order_approved_at": approved,
        "order_delivered_carrier_date": carrier, "order_delivered_customer_date": delivered,
        "order_estimated_delivery_date": estimated,
    })
    canceled = orders.order_status == "canceled"
    orders.loc[canceled, ["order_delivered_carrier_date", "order_delivered_customer_date"]] = pd.NaT

    # ---- items: first 20 orders get exactly 1 item so injected defects are deterministic ----
    weights = 1 / np.arange(1, N_PRODUCTS + 1)
    weights /= weights.sum()
    rows = []
    for i, oid in enumerate(orders.order_id):
        n = 1 if i < 20 else int(rng.integers(1, 4))
        for k in range(1, n + 1):
            rows.append({
                "order_id": oid, "order_item_id": str(k),
                "product_id": products.product_id[rng.choice(N_PRODUCTS, p=weights)],
                "seller_id": sellers.seller_id[rng.integers(0, N_SELLERS)],
                "shipping_limit_date": purchase[i] + pd.Timedelta(days=3),
                "price": round(float(rng.lognormal(4.0, 0.7)), 2),
                "freight_value": round(float(rng.uniform(5, 40)), 2),
            })
    items = pd.DataFrame(rows)
    reviews = pd.DataFrame({
        "review_id": [f"r{i:05d}" for i in range(N_ORDERS)], "order_id": orders.order_id,
        "review_score": rng.integers(1, 6, N_ORDERS).astype(str),
        "review_comment_title": "", "review_comment_message": "",
        "review_creation_date": delivered, "review_answer_timestamp": delivered + pd.Timedelta(days=1),
    })
    payments = pd.DataFrame({
        "order_id": orders.order_id, "payment_sequential": "1", "payment_type": "credit_card",
        "payment_installments": "1", "payment_value": items.groupby("order_id").price.sum().round(2).values,
    })
    geo = pd.DataFrame({
        "geolocation_zip_code_prefix": np.repeat(cust.customer_zip_code_prefix.values[:50], 3),
        "geolocation_lat": rng.uniform(-30, -5, 150).round(6), "geolocation_lng": rng.uniform(-55, -35, 150).round(6),
        "geolocation_city": "sample city", "geolocation_state": "SP",
    })

    # ================= injected dirt (indices 0..19 are single-item orders) =================
    orders.loc[0:1, "order_status"] = "canceled"
    orders.loc[0:1, "order_delivered_customer_date"] = orders.loc[0:1, "order_purchase_timestamp"] + pd.Timedelta(days=9)
    orders.loc[2, "order_delivered_customer_date"] = orders.loc[2, "order_purchase_timestamp"] - pd.Timedelta(days=1)
    # soft flags, on multi-item orders so nothing else is affected: restore clean delivered state first
    soft = [30, 31, 32, 33]
    orders.loc[soft, "order_status"] = "delivered"
    orders.loc[soft, "order_delivered_carrier_date"] = carrier[soft]
    orders.loc[soft, "order_delivered_customer_date"] = delivered[soft]
    orders.loc[30:31, "order_delivered_customer_date"] = pd.NaT           # delivered w/o date
    orders.loc[32, "order_approved_at"] = orders.loc[32, "order_purchase_timestamp"] - pd.Timedelta(hours=2)
    orders.loc[33, "order_delivered_carrier_date"] = delivered[33] + pd.Timedelta(days=1)

    dup_items = items.iloc[[40, 41, 42]]                                   # exact duplicates
    items.loc[items.index[50], "price"] = 0.0
    items.loc[items.index[51], "price"] = 0.0
    items.loc[items.index[52], "freight_value"] = -5.0
    orphans = items.iloc[[60, 61]].assign(order_id=["o_missing_1", "o_missing_2"])
    items = pd.concat([items, dup_items, orphans], ignore_index=True)

    older = reviews.iloc[[100, 101, 102, 103]].copy()                      # older duplicate reviews per order
    older["review_id"] = ["old_" + r for r in older.review_id]
    older["review_answer_timestamp"] = older.review_answer_timestamp - pd.Timedelta(days=30)
    reviews = pd.concat([reviews, older], ignore_index=True)

    tables = {
        "olist_customers_dataset": cust, "olist_sellers_dataset": sellers, "olist_products_dataset": products,
        "product_category_name_translation": translation, "olist_orders_dataset": orders,
        "olist_order_items_dataset": items, "olist_order_reviews_dataset": reviews,
        "olist_order_payments_dataset": payments, "olist_geolocation_dataset": geo,
    }
    outdir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df = df.copy()
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = _ts(df[c])
        df.to_csv(outdir / f"{name}.csv", index=False, lineterminator="\n")
    return tables


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "sample"
    build(out)
    print(f"sample written to {out}")
