"""Run SQL-defined data-quality checks (sql/90_dq/checks.sql) and record results in dq.result.

    python -m sca.dq [layer ...]        # run all layers, or just the ones named
"""
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import db

LAYERS = ["raw", "staging", "core", "marts"]


class DQFailure(Exception):
    """Raised when one or more critical checks fail."""

    def __init__(self, failures: list["CheckResult"]):
        self.failures = failures
        super().__init__("Critical DQ checks failed: " + ", ".join(f"{f.check_id}={f.value}" for f in failures))


@dataclass
class CheckResult:
    check_id: str
    layer: str
    table_name: str
    check_type: str
    severity: str
    value: float | None
    threshold: float
    passed: bool
    error: str | None = None


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def load_checks() -> None:
    db.run_sql_dir("90_dq")


def run_checks(layers: list[str] | None = None, run_id: str | None = None, raise_on_critical: bool = True,
               conn: Connection | None = None) -> list[CheckResult]:
    """Run checks for `layers` (default all). Pass `conn` to run inside a caller-managed transaction."""
    run_id = run_id or new_run_id()
    layers = layers or LAYERS
    if conn is None:
        with db.engine().begin() as c:        # commit results first, so a failing run is still recorded
            results = _execute(c, layers, run_id)
    else:
        results = _execute(conn, layers, run_id)
    failed_critical = [r for r in results if not r.passed and r.severity == "critical"]
    if raise_on_critical and failed_critical:
        raise DQFailure(failed_critical)
    return results


def _execute(conn: Connection, layers: list[str], run_id: str) -> list[CheckResult]:
    load_checks_in(conn)
    rows = conn.execute(
        text("SELECT check_id, layer, table_name, check_type, severity, sql, threshold FROM dq.check "
             "WHERE layer = ANY(:layers) ORDER BY check_id"), {"layers": layers}).all()
    results: list[CheckResult] = []
    for check_id, layer, table_name, check_type, severity, sql, threshold in rows:
        value, error = None, None
        sp = conn.begin_nested()           # a broken check must not poison the surrounding transaction
        try:
            value = conn.exec_driver_sql(sql).scalar()
            sp.commit()
        except Exception as exc:           # missing table / bad SQL counts as a failure
            sp.rollback()
            error = str(exc).splitlines()[0][:300]
        passed = error is None and value is not None and float(value) <= float(threshold)
        results.append(CheckResult(check_id, layer, table_name, check_type, severity,
                                   None if value is None else float(value), float(threshold), passed, error))
    for r in results:
        conn.execute(
            text("INSERT INTO dq.result(run_id, check_id, layer, table_name, check_type, severity, value, threshold, passed, error) "
                 "VALUES (:run_id, :check_id, :layer, :table_name, :check_type, :severity, :value, :threshold, :passed, :error)"),
            {"run_id": run_id, **r.__dict__})
    return results


def load_checks_in(conn: Connection) -> None:
    from .config import SQL_DIR
    conn.exec_driver_sql(db.read_sql(SQL_DIR / "00_schemas.sql"))   # idempotent; ensures dq tables exist
    for f in sorted((SQL_DIR / "90_dq").glob("*.sql")):
        conn.exec_driver_sql(db.read_sql(f))


def summarize(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = "PASS" if r.passed else ("FAIL" if r.severity == "critical" else "WARN")
        lines.append(f"[{mark}] {r.layer:8} {r.check_id:36} value={r.value} (<= {r.threshold:g}){' ERROR: ' + r.error if r.error else ''}")
    return "\n".join(lines)


if __name__ == "__main__":
    res = run_checks(sys.argv[1:] or None, raise_on_critical=False)
    print(summarize(res))
    sys.exit(1 if any(not r.passed and r.severity == "critical" for r in res) else 0)
