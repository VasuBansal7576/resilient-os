from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent import AgentPlanner, ToolInvocation
from detector import FailureDetector
from hydradb_client import HydraDBClient, get_client
from integrations import RealToolRunner
from recovery import RecoveryEngine


MAX_RECOVERY_ATTEMPTS = 4


class ResilientKernel:
    def __init__(
        self,
        memory_client: HydraDBClient | None = None,
        planner: AgentPlanner | None = None,
        tool_runner: Any | None = None,
    ):
        self.agent_id = str(uuid4())[:8]
        self.memory = memory_client or get_client()
        self.detector = FailureDetector()
        self.recovery = RecoveryEngine(self.detector, self.memory)
        self.planner = planner or AgentPlanner()
        self.tool_runner = tool_runner or RealToolRunner()
        self.event_log: list[dict[str, str]] = []
        self.tool_results: list[dict[str, str]] = []

    def run(self, task: str) -> str:
        self.event_log = []
        self.tool_results = []

        plan = self.planner.plan(task)
        self._log_event("agent", "planned", f"{len(plan)} tool calls via {self.planner.last_backend}")

        for invocation in plan:
            self.recovery.save_checkpoint({"tool_results": self.tool_results})
            result = self._execute_with_immunity(invocation)
            self.tool_results.append({"tool": invocation.name, "result": result})

        return self.planner.summarize(task, self.tool_results)

    def _execute_with_immunity(self, invocation: ToolInvocation) -> str:
        from chaos import CHAOS_ENABLED, inject_chaos

        tool_name = invocation.name
        args = invocation.args
        context: dict[str, Any] = {"args": args, "retry_count": 1}
        if self.tool_results:
            context["last_result"] = self.tool_results[-1]["result"]

        try:
            if CHAOS_ENABLED:
                inject_chaos(tool_name)
            result = self._call_tool(tool_name, args, context)
            self.detector.record_call(tool_name, "success")
            self.memory.log_tool_call(self.agent_id, tool_name, args, "success")
            self._log_event(tool_name, "success", result)
            return result
        except Exception as exc:
            self.detector.record_call(tool_name, "error")
            self.memory.log_tool_call(self.agent_id, tool_name, args, "error", str(exc))
            self._log_event(tool_name, "error", str(exc))

            last_error: Exception = exc
            for _ in range(MAX_RECOVERY_ATTEMPTS):
                recovery_result = self.recovery.recover(self.agent_id, tool_name, last_error, context)
                strategy = recovery_result.get("strategy", "none")
                strategy_name = _strategy_name(recovery_result)
                failure_type = recovery_result.get("failure_type", "unknown_error")

                if not recovery_result.get("recovered"):
                    detail = f"{failure_type} unrecovered after {strategy}"
                    self._log_event(tool_name, "unrecovered", detail)
                    return f"[{tool_name} failed: {detail}]"

                if context.get("skip_tool"):
                    self.memory.log_recovery(self.agent_id, failure_type, strategy_name, True, tool=tool_name)
                    detail = f"{failure_type} skipped via {strategy}"
                    self._log_event(tool_name, "recovered", detail)
                    return f"[{tool_name} skipped after recovery: {strategy}]"

                try:
                    if CHAOS_ENABLED and not _recovery_uses_local_path(context):
                        inject_chaos(tool_name)
                    result = self._call_tool(tool_name, args, context)
                    self.detector.record_call(tool_name, "success")
                    self.memory.log_tool_call(self.agent_id, tool_name, args, "recovered")
                    self.memory.log_recovery(self.agent_id, failure_type, strategy_name, True, tool=tool_name)
                    self._log_event(tool_name, "recovered", f"{result} | strategy={strategy}")
                    return result
                except Exception as retry_exc:
                    last_error = retry_exc
                    self.detector.record_call(tool_name, "error")
                    self.memory.log_tool_call(self.agent_id, tool_name, args, "retry_error", str(retry_exc))
                    self.memory.log_recovery(self.agent_id, failure_type, strategy_name, False, tool=tool_name)
                    detail = f"{failure_type} retry failed after {strategy}: {retry_exc}"
                    self._log_event(tool_name, "retry_failed", detail)

            attempted = ", ".join(context.get("attempted_strategies", [])) or "none"
            detail = f"{context.get('failure_type', 'unknown_error')} retry failed after {attempted}: {last_error}"
            self._log_event(tool_name, "unrecovered", detail)
            return f"[{tool_name} failed after recovery: {last_error}]"

    def _call_tool(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> str:
        return self.tool_runner.call(name, args, context or {})

    def _log_event(self, tool: str, status: str, detail: str) -> None:
        self.event_log.append(
            {
                "tool": tool,
                "status": status,
                "detail": detail[:500],
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
        )


def _strategy_name(recovery_result: dict[str, Any]) -> str:
    strategy_name = recovery_result.get("strategy_name")
    if isinstance(strategy_name, str) and strategy_name:
        return strategy_name
    strategy = str(recovery_result.get("strategy", "none"))
    return strategy.removeprefix("hydradb:")


def _recovery_uses_local_path(context: dict[str, Any]) -> bool:
    return any(
        bool(context.get(flag))
        for flag in ("use_backup", "use_cache", "token_refreshed", "chunk_reduced")
    )
