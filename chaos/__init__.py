from __future__ import annotations

from copy import deepcopy

CHAOS_ENABLED = False
_chaos_targets: dict[str, dict[str, object]] = {}


def set_chaos(
    tool: str,
    error_type: str,
    trigger_after: int = 1,
    failures: int = 1,
    repeat: bool = False,
) -> None:
    global CHAOS_ENABLED
    CHAOS_ENABLED = True
    _chaos_targets[tool] = {
        "error_type": error_type,
        "calls": 0,
        "trigger_after": max(1, trigger_after),
        "failures_remaining": max(0, failures),
        "repeat": repeat,
    }


def configure_chaos(rules: list[dict[str, object]]) -> None:
    clear_chaos()
    for rule in rules:
        set_chaos(
            tool=str(rule["tool"]),
            error_type=str(rule["error_type"]),
            trigger_after=int(rule.get("trigger_after", 1)),
            failures=int(rule.get("failures", 1)),
            repeat=bool(rule.get("repeat", False)),
        )


def clear_chaos() -> None:
    global CHAOS_ENABLED
    CHAOS_ENABLED = False
    _chaos_targets.clear()


def get_chaos_targets() -> dict[str, dict[str, object]]:
    return deepcopy(_chaos_targets)


def inject_chaos(tool_name: str) -> None:
    if tool_name not in _chaos_targets:
        return

    target = _chaos_targets[tool_name]
    target["calls"] = int(target["calls"]) + 1
    if int(target["calls"]) < int(target["trigger_after"]):
        return

    repeat = bool(target["repeat"])
    failures_remaining = int(target["failures_remaining"])
    if not repeat and failures_remaining <= 0:
        return
    if not repeat:
        target["failures_remaining"] = failures_remaining - 1

    error_type = str(target["error_type"])
    if error_type == "rate_limit":
        raise RuntimeError("429 Too Many Requests: rate limit exceeded")
    if error_type == "auth":
        raise RuntimeError("401 Unauthorized: token expired or integration not configured")
    if error_type == "timeout":
        raise RuntimeError("Connection timeout after 30s")
    if error_type == "cascade":
        raise RuntimeError("503 Service Unavailable: upstream failed")
    raise RuntimeError(f"Injected chaos: {error_type}")
