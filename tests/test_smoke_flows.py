import pytest

import recovery
from agent import AgentPlanner
from chaos import clear_chaos, set_chaos
from smoke_runner import run_smoke_sequence
from hydradb_client import HydraDBClient
from kernel import ResilientKernel
from tests.doubles import IntegrationTestDouble


def _memory(tmp_path):
    return HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)


@pytest.fixture(autouse=True)
def clean_chaos_state():
    clear_chaos()
    yield
    clear_chaos()


def test_learned_immunity_is_used_across_kernel_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "sleep", lambda _seconds: None)
    memory = _memory(tmp_path)
    task = "Scrape competitor pricing from example.com and notify the team."

    set_chaos("scrape_url", "rate_limit", failures=1)
    first_kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )
    first_result = first_kernel.run(task)

    clear_chaos()
    set_chaos("scrape_url", "rate_limit", failures=1)
    second_kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )
    second_result = second_kernel.run(task)

    assert "Completed 2 of 2 tool calls" in first_result
    assert "Completed 2 of 2 tool calls" in second_result
    assert any("strategy=exponential_backoff" in event["detail"] for event in first_kernel.event_log)
    assert any(
        "strategy=hydradb:exponential_backoff" in event["detail"] for event in second_kernel.event_log
    ), "second run should use the successful recovery persisted by the first run"
    assert len([entry for entry in memory.local_graph if entry["metadata"].get("type") == "recovery"]) >= 2, (
        "successful recoveries should be persisted as immunity memories"
    )


def test_notification_auth_failure_attempts_recovery(tmp_path):
    memory = _memory(tmp_path)
    set_chaos("send_notification", "auth", failures=1)

    kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )
    result = kernel.run("Scrape competitor pricing from example.com and notify Telegram.")

    assert "Completed 2 of 2 tool calls" in result
    assert any(
        event["tool"] == "send_notification"
        and event["status"] == "recovered"
        and "strategy=refresh_token" in event["detail"]
        for event in kernel.event_log
    )


def test_cascade_failures_use_fallbacks_and_continue(tmp_path):
    memory = _memory(tmp_path)
    set_chaos("scrape_url", "cascade", failures=1)
    set_chaos("send_notification", "cascade", failures=1)

    kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )
    result = kernel.run("Scrape competitor pricing from example.com and notify Discord.")

    assert "Completed 2 of 2 tool calls" in result
    assert "cached ScrapeGraphAI result" in kernel.tool_results[0]["result"]
    assert [
        (event["tool"], event["status"])
        for event in kernel.event_log
        if event["status"] == "recovered"
    ] == [("scrape_url", "recovered"), ("send_notification", "recovered")]
    recovery_failure_types = {
        entry["metadata"]["failure_type"] for entry in memory.local_graph if entry["metadata"].get("type") == "recovery"
    }
    assert recovery_failure_types == {"cascade_failure"}, "successful cascade recoveries should be persisted"


def test_smoke_sequence_exercises_clean_infection_immunity_auth_and_cascade(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "sleep", lambda _seconds: None)

    runs = run_smoke_sequence(graph_path=tmp_path / "smoke_graph.jsonl", tool_runner=IntegrationTestDouble())

    assert [run.name for run in runs] == [
        "clean",
        "rate_limit_first_infection",
        "rate_limit_learned_antibody",
        "auth_expiry",
        "cascade",
    ]
    assert all("Completed" in run.result for run in runs)
    assert not any(event["status"] == "recovered" for event in runs[0].events)
    assert any("strategy=exponential_backoff" in event["detail"] for event in runs[1].events)
    assert any("strategy=hydradb:exponential_backoff" in event["detail"] for event in runs[2].events)
    assert any("strategy=refresh_token" in event["detail"] for event in runs[3].events)
    cascade_recoveries = [event for event in runs[4].events if event["status"] == "recovered"]
    assert cascade_recoveries
    assert all(
        "hydradb:refresh_token" not in event["detail"] for event in cascade_recoveries
    ), "cascade recovery should not reuse the learned auth refresh-token strategy"
    assert any("cached ScrapeGraphAI result" in event["detail"] for event in cascade_recoveries)
