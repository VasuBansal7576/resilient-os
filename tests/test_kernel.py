from agent import AgentPlanner
from chaos import clear_chaos, set_chaos
from hydradb_client import HydraDBClient
from kernel import ResilientKernel
from tests.doubles import IntegrationTestDouble


def test_kernel_runs_clean_task(tmp_path):
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )

    result = kernel.run("Scrape competitor pricing from example.com and notify the team.")

    assert "Completed 2 of 2 tool calls" in result
    assert [event["status"] for event in kernel.event_log].count("success") == 2


def test_kernel_recovers_from_rate_limit_and_logs_immunity(tmp_path):
    clear_chaos()
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    set_chaos("scrape_url", "rate_limit", failures=1)

    kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )
    result = kernel.run("Scrape competitor pricing from example.com and notify the team.")

    clear_chaos()

    assert "Completed 2 of 2 tool calls" in result
    assert any(event["status"] == "recovered" for event in kernel.event_log)
    assert any(entry["metadata"].get("type") == "recovery" for entry in memory.local_graph)


def test_kernel_tries_next_strategy_after_retry_failure(tmp_path):
    clear_chaos()
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    set_chaos("scrape_url", "rate_limit", failures=1, repeat=True)

    try:
        kernel = ResilientKernel(
            memory_client=memory,
            planner=AgentPlanner(mode="scripted"),
            tool_runner=IntegrationTestDouble(),
        )
        result = kernel.run("Scrape competitor pricing from example.com and notify the team.")
    finally:
        clear_chaos()

    recoveries = [
        entry["metadata"]
        for entry in memory.local_graph
        if entry.get("metadata", {}).get("type") == "recovery"
    ]

    assert "Completed 2 of 2 tool calls" in result
    assert any(event["status"] == "retry_failed" for event in kernel.event_log)
    assert any(
        item["strategy"] == "exponential_backoff" and item["success"] is False for item in recoveries
    )
    assert any(item["strategy"] == "switch_to_backup" and item["success"] is True for item in recoveries)


def test_kernel_does_not_use_auth_antibody_for_cascade(tmp_path):
    clear_chaos()
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    memory.log_recovery("previous-agent", "auth_failure", "refresh_token", True, tool="scrape_url")
    set_chaos("scrape_url", "cascade", failures=1)

    try:
        kernel = ResilientKernel(
            memory_client=memory,
            planner=AgentPlanner(mode="scripted"),
            tool_runner=IntegrationTestDouble(),
        )
        result = kernel.run("Scrape competitor pricing from example.com and notify the team.")
    finally:
        clear_chaos()

    assert "Completed 2 of 2 tool calls" in result
    assert any(
        event["status"] == "recovered" and "strategy=circuit_breaker" in event["detail"]
        for event in kernel.event_log
    )
