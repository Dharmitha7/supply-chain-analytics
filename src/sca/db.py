from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import SQL_DIR, db_url


@lru_cache
def engine() -> Engine:
    return create_engine(db_url(), future=True)


def run_sql_file(path: Path) -> None:
    with engine().begin() as conn:
        conn.exec_driver_sql(path.read_text(encoding="utf-8"))


def run_sql_dir(subdir: str) -> list[str]:
    """Run every .sql file in sql/<subdir> in filename order. Returns the names run."""
    files = sorted((SQL_DIR / subdir).glob("*.sql"))
    for f in files:
        run_sql_file(f)
    return [f.name for f in files]


def log_step(step: str, table: str | None, rows_in: int | None, rows_out: int | None) -> None:
    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dq.run_log(step, table_name, rows_in, rows_out) "
                "VALUES (:s, :t, :i, :o)"
            ),
            {"s": step, "t": table, "i": rows_in, "o": rows_out},
        )
