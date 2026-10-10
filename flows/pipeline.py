"""Prefect flow: the whole pipeline, gated by data-quality checks at every layer.

    python flows/pipeline.py                     # run once against data/raw
    python flows/pipeline.py data/sample         # run once against another folder (CI uses the sample)

Order: ingest -> staging -> DQ(raw, staging) -> core dims/facts -> simulate -> sim facts -> DQ(core)
       -> marts -> DQ(marts). Simulation runs after the core order facts because its demand is real sales.
A failing CRITICAL check raises DQFailure, which fails the task and the flow; the previous marts stay intact.
The pipeline is idempotent: every layer is dropped and rebuilt on each run.
"""
import sys
from pathlib import Path

from prefect import flow, get_run_logger, task

from sca import db, dq, ingest, simulate
from sca.config import RAW_DIR


@task(name="ingest_raw", retries=2, retry_delay_seconds=30)
def ingest_raw(raw_dir: str) -> dict:
    return ingest.run(Path(raw_dir))


@task(name="sql_layer")
def sql_layer(subdir: str) -> list[str]:
    done = db.run_sql_dir(subdir)
    get_run_logger().info("ran %s: %s", subdir, ", ".join(done))
    return done


@task(name="simulate_inventory")
def simulate_inventory() -> dict:
    return simulate.run()


@task(name="dq_gate")
def dq_gate(layers: list[str], run_id: str) -> dict:
    log = get_run_logger()
    results = dq.run_checks(layers, run_id=run_id, raise_on_critical=False)
    for r in results:
        if not r.passed:
            log.warning("DQ %s %s value=%s threshold=%s %s", r.severity.upper(), r.check_id, r.value, r.threshold, r.error or "")
    failed = [r for r in results if not r.passed and r.severity == "critical"]
    if failed:
        raise dq.DQFailure(failed)
    return {"checks": len(results), "warnings": sum(1 for r in results if not r.passed)}


@flow(name="daily_refresh", log_prints=True)
def daily_refresh(raw_dir: str = str(RAW_DIR)) -> dict:
    run_id = dq.new_run_id()
    summary: dict = {"dq_run_id": run_id}
    summary["ingest"] = ingest_raw(raw_dir)
    sql_layer("10_staging")
    summary["dq_raw_staging"] = dq_gate(["raw", "staging"], run_id)
    sql_layer("20_core_dims")
    sql_layer("30_core_facts")
    summary["simulate"] = simulate_inventory()
    sql_layer("35_core_sim_facts")
    summary["dq_core"] = dq_gate(["core"], run_id)
    sql_layer("40_marts")
    summary["dq_marts"] = dq_gate(["marts"], run_id)
    print(summary)
    return summary


if __name__ == "__main__":
    daily_refresh(sys.argv[1] if len(sys.argv) > 1 else str(RAW_DIR))
