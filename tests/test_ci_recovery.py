"""Fault injection for CI: recover transient jobs without hiding real failures."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import diagnose_bq_jobs as diagnostic
from scripts import run_dbt_ci as runner


def node(name, status="error", message=None):
    return {"unique_id": f"model.kiln.{name}", "status": status, "execution_time": 91,
            "message": message if message is not None else f"Job ID: job-{name}"}


def job(reason="stopped", message="Job execution was cancelled: Job timed out after 1 min 30 sec",
        state="DONE"):
    started = datetime(2026, 10, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        job_id="job-a", project="test-project", location="US", state=state,
        created=started - timedelta(milliseconds=200), started=started,
        ended=started + timedelta(seconds=91), slot_millis=None,
        error_result={"reason": reason, "message": message},
        to_api_repr=lambda: {"status": {"state": state, "errorResult": {"reason": reason}}},
    )


@pytest.mark.parametrize("reason,message,state,expected", [
    ("stopped", "Job execution was cancelled: Job timed out after 1 min 30 sec", "DONE", True),
    ("backendError", "Retrying may solve the problem", "DONE", True),
    ("internalError", "Internal error", "DONE", True),
    ("rateLimitExceeded", "Too many table updates", "DONE", True),
    ("jobRateLimitExceeded", "Too many jobs", "DONE", True),
    ("stopped", "User requested cancellation", "DONE", False),
    ("stopped", "Job timed out", "RUNNING", False),
    ("backendError", "Backend error", "PENDING", False),
    ("invalidQuery", "Syntax error", "DONE", False),
    ("accessDenied", "Access denied", "DONE", False),
    ("usageQuotaExceeded", "Daily query cap", "DONE", False),
    ("quotaExceeded", "Query limit", "DONE", False),
])
def test_only_completed_transient_jobs_are_retryable(reason, message, state, expected):
    assert diagnostic.retryable_job(job(reason, message, state)) is expected


def test_diagnostics_save_evidence_and_preserve_unknown_metrics(monkeypatch, tmp_path, capsys):
    client = Mock()
    client.get_job.return_value = job()
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(diagnostic.bigquery, "Client", lambda **kwargs: client)
    assert diagnostic.diagnose([node("a")], tmp_path) == {"model.kiln.a"}
    client.get_job.assert_called_once_with("job-a", timeout=10, retry=None)
    assert json.loads((tmp_path / "job-a.json").read_text())["status"]["state"] == "DONE"
    assert "slot_ms=None" in capsys.readouterr().out


def test_diagnostic_auth_failure_is_not_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(diagnostic.bigquery, "Client", Mock(side_effect=RuntimeError("no auth")))
    assert diagnostic.diagnose([node("a")], tmp_path) == set()


def test_assertion_failure_never_contacts_bigquery(monkeypatch, tmp_path):
    client = Mock(side_effect=AssertionError("must not authenticate"))
    monkeypatch.setattr(diagnostic.bigquery, "Client", client)
    assert diagnostic.diagnose([node("a", "fail")], tmp_path) == set()
    client.assert_not_called()


def simulate(monkeypatch, tmp_path, passes, *, stale=False):
    """Emulate dbt's artifact contract, including per-invocation replacement."""
    results_path = tmp_path / "target" / "run_results.json"
    results_path.parent.mkdir(exist_ok=True)
    commands = []

    def dbt_run(command, cwd, timeout):
        assert cwd == tmp_path and timeout > 0
        commands.append(command)
        returncode, results = passes[len(commands) - 1]
        if not stale:
            results_path.write_text(json.dumps({
                "metadata": {"invocation_id": str(len(commands))}, "results": results,
            }))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(runner.subprocess, "run", dbt_run)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    # Warehouse lookup is tested above; this fixture controls which failures
    # the server confirms as transient independently of dbt's exit status.
    monkeypatch.setattr(runner, "diagnose", lambda results, output: {
        r["unique_id"] for r in results if r["status"] == "error" and "Job ID:" in r["message"]
    })
    return commands, results_path


def test_success_needs_no_retries(monkeypatch, tmp_path):
    commands, _ = simulate(monkeypatch, tmp_path, [(0, [node("a", "success"), node("b", "warn")])])
    assert runner.run_build(tmp_path) == 0
    assert commands == [["dbt", "build"]]


def test_successive_downstream_stalls_each_get_recovery(monkeypatch, tmp_path):
    passes = [
        (1, [node("a"), node("b", "skipped"), node("c", "skipped")]),
        (1, [node("a", "success"), node("b"), node("c", "skipped")]),
        (1, [node("b", "success"), node("c")]),
        (0, [node("c", "success")]),
    ]
    commands, _ = simulate(monkeypatch, tmp_path, passes)
    assert runner.run_build(tmp_path) == 0
    assert commands == [["dbt", "build"]] + [["dbt", "retry"]] * 3
    history = tmp_path / "target" / "ci_attempts"
    assert len(list(history.glob("*/run_results.json"))) == 4
    assert json.loads((history / "01_build" / "run_results.json").read_text())["results"] == passes[0][1]


def test_persistent_failure_stops_after_two_retries_and_saves_last_attempt(monkeypatch, tmp_path):
    commands, _ = simulate(monkeypatch, tmp_path, [(1, [node("a")])] * 3)
    assert runner.run_build(tmp_path) == 1
    assert len(commands) == 3
    assert (tmp_path / "target/ci_attempts/03_retry/run_results.json").exists()


@pytest.mark.parametrize("results", [
    [node("a", "fail", "Got 2 results, configured to fail")],
    [node("a", message="Syntax error at [1:1]")],
    [node("a"), node("b", "fail", "Ledger does not balance")],
    [node("a", "skipped")],
])
def test_non_transient_failures_do_not_retry(monkeypatch, tmp_path, results):
    commands, _ = simulate(monkeypatch, tmp_path, [(1, results)])
    assert runner.run_build(tmp_path) == 1
    assert len(commands) == 1


def test_stale_results_cannot_turn_a_failed_process_green(monkeypatch, tmp_path):
    commands, path = simulate(monkeypatch, tmp_path, [(1, [])], stale=True)
    path.write_text(json.dumps({"results": [node("a", "success")]}))
    assert runner.run_build(tmp_path) == 1
    assert len(commands) == 1


def test_zero_exit_with_failed_assertion_is_still_red(monkeypatch, tmp_path):
    simulate(monkeypatch, tmp_path, [(0, [node("a", "fail")])])
    assert runner.run_build(tmp_path) == 1


def test_build_deadline_stops_recovery(monkeypatch, tmp_path):
    simulate(monkeypatch, tmp_path, [])
    process = Mock(side_effect=subprocess.TimeoutExpired(["dbt", "build"], 600))
    monkeypatch.setattr(runner.subprocess, "run", process)
    assert runner.run_build(tmp_path) == 1
    process.assert_called_once()
