from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent import AgentPlanner
from chaos import clear_chaos, configure_chaos
from hydradb_client import HydraDBClient
from kernel import ResilientKernel

DEFAULT_TASK = "Scrape competitor pricing from example.com and notify the team."


@dataclass
class SmokeRun:
    name: str
    result: str
    events: list[dict[str, str]]
    memory_events: int
    recoveries: int
    immunity: int


def run_smoke_sequence(
    task: str = DEFAULT_TASK,
    graph_path: str | Path = ".resilient_os/smoke_runner_graph.jsonl",
    reset_memory: bool = True,
    tool_runner: Any | None = None,
) -> list[SmokeRun]:
    memory = HydraDBClient(api_key="", local_path=graph_path, online=False)
    if reset_memory:
        memory.clear_local_graph()

    scenarios = [
        ("clean", []),
        ("rate_limit_first_infection", [{"tool": "scrape_url", "error_type": "rate_limit", "failures": 1}]),
        ("rate_limit_learned_antibody", [{"tool": "scrape_url", "error_type": "rate_limit", "failures": 1}]),
        ("auth_expiry", [{"tool": "send_notification", "error_type": "auth", "failures": 1}]),
        (
            "cascade",
            [
                {"tool": "scrape_url", "error_type": "cascade", "failures": 1},
                {"tool": "send_notification", "error_type": "cascade", "failures": 1},
            ],
        ),
    ]

    runs: list[SmokeRun] = []
    for name, chaos_rules in scenarios:
        configure_chaos(chaos_rules)
        kernel = ResilientKernel(
            memory_client=memory,
            planner=AgentPlanner(mode="scripted"),
            tool_runner=tool_runner,
        )
        result = kernel.run(task)
        runs.append(_build_smoke_run(name, result, kernel.event_log, memory.local_graph))
        clear_chaos()

    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ResilientOS terminal smoke sequence.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--graph-path", default=".resilient_os/smoke_runner_graph.jsonl")
    parser.add_argument("--keep-memory", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    runs = run_smoke_sequence(args.task, args.graph_path, reset_memory=not args.keep_memory)
    if args.json:
        print(json.dumps([asdict(run) for run in runs], indent=2))
        return

    for run in runs:
        print(f"\n== {run.name} ==")
        print(run.result)
        print(f"memory={run.memory_events} recoveries={run.recoveries} immunity={run.immunity}")
        for event in run.events:
            print(f"- {event['status'].upper()} {event['tool']}: {event['detail']}")


def _build_smoke_run(
    name: str,
    result: str,
    events: list[dict[str, str]],
    memory_graph: list[dict[str, Any]],
) -> SmokeRun:
    recoveries = [entry for entry in memory_graph if entry.get("metadata", {}).get("type") == "recovery"]
    immunity = [entry for entry in recoveries if entry.get("metadata", {}).get("success") is True]
    return SmokeRun(
        name=name,
        result=result,
        events=events,
        memory_events=len(memory_graph),
        recoveries=len(recoveries),
        immunity=len(immunity),
    )


if __name__ == "__main__":
    main()
