"""The Prefect flow end to end on the sample data, and proof that a critical DQ failure fails the run."""
import shutil

import pytest
from prefect.testing.utilities import prefect_test_harness

from sca import dq

from .conftest import SAMPLE_DIR, scalar


@pytest.fixture(scope="module", autouse=True)
def prefect_harness():
    with prefect_test_harness():
        yield


def test_flow_completes_and_records_dq_run(engine):
    from pipeline import daily_refresh
    summary = daily_refresh(str(SAMPLE_DIR))
    assert summary["dq_core"]["checks"] > 0 and summary["dq_marts"]["checks"] == 8
    rid = summary["dq_run_id"]
    assert scalar(engine, f"SELECT count(*) FROM dq.result WHERE run_id = '{rid}'") == 29 + 17 + 8
    assert scalar(engine, f"SELECT count(*) FROM dq.result WHERE run_id = '{rid}' AND NOT passed AND severity = 'critical'") == 0
    assert scalar(engine, "SELECT count(*) FROM marts.dq_latest") > 0
    assert scalar(engine, "SELECT count(DISTINCT run_id) FROM marts.dq_latest") == 1


def test_flow_is_idempotent(engine):
    from pipeline import daily_refresh
    before = scalar(engine, "SELECT count(*) FROM core.fact_order_line")
    daily_refresh(str(SAMPLE_DIR))
    assert scalar(engine, "SELECT count(*) FROM core.fact_order_line") == before


def test_critical_failure_fails_the_flow_and_is_recorded(engine, tmp_path):
    from pipeline import daily_refresh
    broken = tmp_path / "broken"
    shutil.copytree(SAMPLE_DIR, broken)
    orders = broken / "olist_orders_dataset.csv"
    orders.write_text(orders.read_text().splitlines()[0] + "\n")      # header only: raw.orders empty
    with pytest.raises(dq.DQFailure):
        daily_refresh(str(broken))
    assert scalar(engine, "SELECT count(*) FROM dq.result WHERE check_id = 'RAW_ORDERS_NOT_EMPTY' AND NOT passed") >= 1
    # restore a healthy state for any later test
    daily_refresh(str(SAMPLE_DIR))
