from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone


class FailureDetector:
    def __init__(self):
        self.call_log: dict[str, list[dict[str, object]]] = defaultdict(list)

    def record_call(self, tool: str, status: str) -> None:
        self.call_log[tool].append(
            {
                "timestamp": datetime.now(timezone.utc),
                "status": status,
            }
        )

    def is_infinite_loop(self, tool: str, window_seconds: int = 30, threshold: int = 3) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        recent_errors = [
            call
            for call in self.call_log[tool]
            if call["timestamp"] > cutoff and call["status"] == "error"
        ]
        return len(recent_errors) >= threshold

    def is_cascade(self, tools: list[str] | None = None, window_seconds: int = 60) -> bool:
        observed_tools = tools or list(self.call_log.keys())
        if not observed_tools:
            return False

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        failed_tools = {
            tool
            for tool in observed_tools
            if any(call["timestamp"] > cutoff and call["status"] == "error" for call in self.call_log[tool])
        }
        return len(failed_tools) >= max(2, round(len(observed_tools) * 0.6))

    def diagnose(self, tool: str, error: Exception) -> str:
        error_str = str(error).lower()

        if "rate limit" in error_str or "429" in error_str:
            return "rate_limit"
        if "auth" in error_str or "token" in error_str or "401" in error_str or "403" in error_str:
            return "auth_failure"
        if "timeout" in error_str:
            return "timeout"
        if "503" in error_str or "upstream" in error_str or "service unavailable" in error_str:
            return "cascade_failure"
        if "security policy" in error_str or "blocked unsafe" in error_str or "non-public ip" in error_str:
            return "security_policy"
        if "not configured" in error_str or "api key" in error_str or "api_key" in error_str or "missing" in error_str:
            return "auth_failure"
        if self.is_infinite_loop(tool):
            return "infinite_retry_loop"
        return "unknown_error"
