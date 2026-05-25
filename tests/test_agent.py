import pytest

from agent import AgentPlanner
from hydradb_client import HydraDBClient
from kernel import ResilientKernel
from tests.doubles import IntegrationTestDouble


def test_scripted_planner_builds_expected_real_extraction_workflow():
    planner = AgentPlanner(mode="scripted")

    plan = planner.plan("Scrape competitor pricing from example.com and notify the team.")

    assert planner.last_backend == "scripted"
    assert [step.name for step in plan] == ["scrape_url", "send_notification"]
    assert plan[0].args["url"] == "https://example.com"
    assert "pricing" in plan[0].args["prompt"].lower()
    assert plan[1].args["include_last_result"] is True


def test_scripted_planner_uses_real_scrape_for_unspecified_tasks():
    planner = AgentPlanner(mode="scripted")

    plan = planner.plan("Handle the daily resilience check.")

    assert [step.name for step in plan] == ["scrape_url"]
    assert plan[0].args["url"] == "https://example.com"


@pytest.mark.parametrize(
    ("task", "expected_url"),
    [
        ("Scrape pricing from https://pricing.example.com/plans.", "https://pricing.example.com/plans"),
        ("Scrape pricing from https://vendor.example/pricing, then write a report.", "https://vendor.example/pricing"),
        ("Scrape competitor pricing from vendor.example and notify Telegram.", "https://vendor.example"),
    ],
)
def test_url_extraction_is_visible_through_scripted_planner(task, expected_url):
    planner = AgentPlanner(mode="scripted")

    plan = planner.plan(task)

    assert plan[0].name == "scrape_url"
    assert plan[0].args["url"] == expected_url


def test_url_extraction_flows_through_kernel_public_results(tmp_path):
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    kernel = ResilientKernel(
        memory_client=memory,
        planner=AgentPlanner(mode="scripted"),
        tool_runner=IntegrationTestDouble(),
    )

    kernel.run("Scrape competitor pricing from https://pricing.example.com/plans.")

    assert kernel.tool_results[0] == {
        "tool": "scrape_url",
        "result": (
            'ScrapeGraphAI[test-double] extracted from https://pricing.example.com/plans: '
            '{"competitor": "Acme", "price": "$99", "source": "https://pricing.example.com/plans", '
            '"url": "https://pricing.example.com/plans"}'
        ),
    }
