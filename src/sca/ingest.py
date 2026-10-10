"""Load Olist CSVs into the raw schema as untouched text. Idempotent: each table is replaced."""
import io
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from . import db
from .config import RAW_DIR, SQL_DIR

# csv file -> raw table
FILES = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "category_translation",
}


def _copy_in(conn, table: str, df: pd.DataFrame) -> None:
    cols = ", ".join(f'"{c}"' for c in df.columns)
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with conn.connection.cursor() as cur:
        with cur.copy(f"COPY raw.{table} ({cols}) FROM STDIN WITH (FORMAT csv)") as cp:
            while chunk := buf.read(1 << 20):
                cp.write(chunk)


def load_table(csv: Path, table: str) -> int:
    # dtype=str + keep_default_na=False: raw layer keeps values exactly as delivered
    df = pd.read_csv(csv, dtype=str, keep_default_na=False, encoding="utf-8")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    col_defs = ", ".join(f'"{c}" TEXT' for c in df.columns)
    with db.engine().begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS raw.{table}"))
        conn.execute(text(f"CREATE TABLE raw.{table} ({col_defs})"))
        _copy_in(conn, table, df)
    db.log_step("ingest", f"raw.{table}", len(df), len(df))
    return len(df)


def run(raw_dir: Path = RAW_DIR) -> dict[str, int]:
    db.run_sql_file(SQL_DIR / "00_schemas.sql")
    missing = [f for f in FILES if not (raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing in {raw_dir}: {missing}")
    counts = {t: load_table(raw_dir / f, t) for f, t in FILES.items()}
    for t, n in counts.items():
        print(f"raw.{t}: {n:,} rows")
    return counts


if __name__ == "__main__":
    run(Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_DIR)
