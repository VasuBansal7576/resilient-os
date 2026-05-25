from __future__ import annotations

from typing import Any

import streamlit as st

from agent import AgentPlanner
from chaos import clear_chaos, configure_chaos
from hydradb_client import get_client, get_local_graph
from kernel import ResilientKernel

DEFAULT_TASK = "Scrape competitor pricing from example.com and notify the team."


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "chaos_rules": [],
        "last_events": [],
        "last_result": "",
        "last_backend": "scripted",
        "last_mode": "scripted",
        "last_run_chaos": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _tool_label(tool: str | None) -> str:
    labels = {
        "agent": "Agent planner",
        "scrape_url": "ScrapeGraphAI scraper",
        "send_notification": "Notifier",
    }
    return labels.get(tool or "", (tool or "Unknown tool").replace("_", " ").title())


def _fault_label(error_type: str | None) -> str:
    labels = {
        "rate_limit": "rate limit",
        "auth": "expired auth token",
        "timeout": "timeout",
        "cascade": "cascade failure",
    }
    return labels.get(error_type or "", (error_type or "unknown fault").replace("_", " "))


def _rule_text(rule: dict[str, Any]) -> str:
    mode = "repeating" if rule.get("repeat") else "one-shot"
    failures = int(rule.get("failures", 1))
    return (
        f"{_tool_label(str(rule.get('tool')))} will raise "
        f"{_fault_label(str(rule.get('error_type')))} "
        f"for {failures} call{'s' if failures != 1 else ''} ({mode})."
    )


def _recovery_entries(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in graph if entry.get("metadata", {}).get("type") == "recovery"]


def _successful_recoveries(graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in _recovery_entries(graph)
        if entry.get("metadata", {}).get("success") is True
    ]


def _latest_event(events: list[dict[str, str]], status: str) -> dict[str, str] | None:
    return next((event for event in reversed(events) if event.get("status") == status), None)


def _events_with_status(events: list[dict[str, str]], status: str) -> list[dict[str, str]]:
    return [event for event in events if event.get("status") == status]


def _run_status(events: list[dict[str, str]], result: str) -> tuple[str, str]:
    if not events and not result:
        return "Ready", "Clean baseline has not run yet."
    if any(event.get("status") == "unrecovered" for event in events):
        return "Needs attention", "A tool still failed after recovery."
    if any(event.get("status") == "recovered" for event in events):
        return "Recovered", "Failure was detected and the run completed."
    if any(event.get("status") == "error" for event in events):
        return "Diagnosed", "Failure was detected during the run."
    return "Clean", "All planned tool calls completed."


def _backend_status(client: Any) -> tuple[str, str]:
    if client.online:
        return "HydraDB", f"HydraDB online for tenant {client.tenant_id}."
    if client.api_key:
        return "Local JSONL", f"HydraDB unreachable; writing to {client.local_path}."
    return "Local JSONL", f"No HydraDB key set; writing to {client.local_path}."


def _event_line(event: dict[str, str]) -> str:
    status = event.get("status", "unknown").upper()
    tool = _tool_label(event.get("tool"))
    return f"{status} - {tool} at {event.get('time', '--:--:--')}: {event.get('detail', '')}"


def _render_status_panel() -> None:
    client = get_client()
    graph = get_local_graph()
    events = st.session_state.last_events
    recoveries = _recovery_entries(graph)
    successful_recoveries = _successful_recoveries(graph)
    last_run_recoveries = _events_with_status(events, "recovered")
    immunity_count = max(len(successful_recoveries), len(last_run_recoveries))
    backend_label, backend_detail = _backend_status(client)
    run_label, run_detail = _run_status(events, st.session_state.last_result)
    recovered_event = _latest_event(events, "recovered")

    recovered_value = _tool_label(recovered_event.get("tool")) if recovered_event else "None"
    recovered_detail = (
        _event_line(recovered_event)
        if recovered_event
        else "Recovered event will appear after chaos is handled."
    )

    c1, c2, c3, c4 = st.columns([1.05, 1, 1, 1.2], gap="medium")
    c1.metric("Memory backend", backend_label)
    c1.caption(f"Backend status: {backend_detail}")
    c2.metric("Immunity counter", immunity_count)
    c2.caption(
        f"{len(successful_recoveries)} successful recovery memories stored; "
        f"{len(last_run_recoveries)} recovered in last run."
    )
    c3.metric("Last run", run_label)
    c3.caption(run_detail)
    c4.metric("Last recovered event", recovered_value)
    c4.caption(recovered_detail)


def _render_chaos_controls() -> None:
    st.subheader("Chaos Injection")
    repeat_failures = st.toggle(
        "Repeat until cleared",
        value=False,
        help="Use one-shot to validate recovery. Repeat keeps the fault active.",
    )

    if st.button("Arm rate limit on ScrapeGraphAI", use_container_width=True):
        st.session_state.chaos_rules = [
            {
                "tool": "scrape_url",
                "error_type": "rate_limit",
                "failures": 1,
                "repeat": repeat_failures,
            }
        ]
    if st.button("Arm notification auth failure", use_container_width=True):
        st.session_state.chaos_rules = [
            {
                "tool": "send_notification",
                "error_type": "auth",
                "failures": 1,
                "repeat": repeat_failures,
            }
        ]
    if st.button("Arm cascade failure", use_container_width=True):
        st.session_state.chaos_rules = [
            {
                "tool": "scrape_url",
                "error_type": "cascade",
                "failures": 1,
                "repeat": repeat_failures,
            },
            {
                "tool": "send_notification",
                "error_type": "cascade",
                "failures": 1,
                "repeat": repeat_failures,
            },
        ]
    if st.button("Clear chaos for clean run", use_container_width=True):
        st.session_state.chaos_rules = []
        clear_chaos()

    active_rules = st.session_state.chaos_rules
    st.markdown("**Active chaos**")
    if active_rules:
        for rule in active_rules:
            st.warning(f"Chaos injection armed: {_rule_text(rule)}")
    else:
        st.success("No active chaos. Next run is clean.")

    last_run_chaos = st.session_state.last_run_chaos
    st.markdown("**Chaos used in last run**")
    if last_run_chaos:
        for rule in last_run_chaos:
            st.write(f"Last run chaos: {_rule_text(rule)}")
    else:
        st.write("Last run chaos: none.")


def _render_memory_panel() -> None:
    st.subheader("Memory")
    client = get_client()
    graph = get_local_graph()
    recoveries = _recovery_entries(graph)
    successful_recoveries = _successful_recoveries(graph)

    m1, m2, m3 = st.columns(3)
    m1.metric("Memory events", len(graph))
    m2.metric("Recoveries", len(recoveries))
    m3.metric("Successful antibodies", len(successful_recoveries))

    backend_label, backend_detail = _backend_status(client)
    st.write(f"Memory backend status: {backend_label}. {backend_detail}")

    if st.button("Reset local memory", use_container_width=True):
        client.clear_local_graph()
        st.session_state.last_events = []
        st.session_state.last_result = ""
        st.session_state.last_run_chaos = []
        st.rerun()


def _render_run_controls() -> None:
    st.subheader("Run Agent")
    task = st.text_area("Task", value=DEFAULT_TASK, height=92)
    mode = st.radio(
        "Planner",
        options=["scripted", "auto", "llm"],
        index=0,
        horizontal=True,
        help="scripted creates a deterministic real-tool plan. auto tries Groq first, then NVIDIA.",
    )

    run_clicked = st.button("Run under ResilientOS", type="primary", use_container_width=True)
    if run_clicked:
        st.session_state.last_run_chaos = [dict(rule) for rule in st.session_state.chaos_rules]
        configure_chaos(st.session_state.chaos_rules)
        planner = AgentPlanner(mode=mode)
        kernel = ResilientKernel(planner=planner)
        with st.spinner("Intercepting tool calls and recording memory..."):
            st.session_state.last_result = kernel.run(task)
            st.session_state.last_events = kernel.event_log
            st.session_state.last_backend = planner.last_backend
            st.session_state.last_mode = mode
        if not any(rule.get("repeat") for rule in st.session_state.chaos_rules):
            clear_chaos()
            st.session_state.chaos_rules = []
        st.rerun()

    if st.session_state.last_result:
        st.success(f"Run result: {st.session_state.last_result}")
        st.write(
            f"Planner backend status: requested {st.session_state.last_mode}, "
            f"used {st.session_state.last_backend}."
        )


def _render_execution_story() -> None:
    st.subheader("Execution Story")
    events = st.session_state.last_events
    if not events:
        st.info("Execution story: no run recorded yet.")
        return

    for index, event in enumerate(events, start=1):
        line = f"{index}. {_event_line(event)}"
        status = event.get("status")
        if status == "success":
            st.success(line)
        elif status == "recovered":
            st.success(f"Recovered event: {line}")
        elif status in {"error", "unrecovered"}:
            st.error(line)
        else:
            st.info(line)


def _render_antibodies() -> None:
    st.subheader("Recent Antibodies")
    antibodies = [
        {
            "failure_type": entry.get("metadata", {}).get("failure_type"),
            "strategy": entry.get("metadata", {}).get("strategy"),
            "success": entry.get("metadata", {}).get("success"),
            "timestamp": entry.get("timestamp"),
        }
        for entry in _recovery_entries(get_local_graph())
    ]

    if not antibodies:
        st.info("No recovery antibodies logged yet.")
        return

    for antibody in antibodies[-8:]:
        outcome = "worked" if antibody["success"] else "failed"
        st.write(
            "Antibody memory: "
            f"{antibody['failure_type']} -> {antibody['strategy']} "
            f"({outcome}) at {antibody['timestamp']}."
        )


st.set_page_config(page_title="ResilientOS Demo", layout="wide")
_init_state()

st.title("ResilientOS Demo Console")
st.caption("Clean run, controlled chaos, recovery, and memory-backed immunity in one local dashboard.")

story = st.container()
with story:
    st.markdown("**Hackathon story:** Clean run -> chaos injection -> recovered event -> immunity counter.")
    _render_status_panel()

left, right = st.columns([2, 1], gap="large")

with left:
    _render_run_controls()
    st.divider()
    _render_execution_story()
    st.divider()
    _render_antibodies()

with right:
    _render_chaos_controls()
    st.divider()
    _render_memory_panel()
