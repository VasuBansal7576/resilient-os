from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from hydradb_client import KNOWN_FAILURE_TYPES, HydraDBClient, get_client


RECOVERY_STRATEGIES: dict[str, list[dict[str, Any]]] = {
    "rate_limit": [
        {"name": "exponential_backoff", "action": "wait", "params": {"seconds": 0.2}},
        {"name": "switch_to_backup", "action": "fallback", "params": {}},
    ],
    "auth_failure": [
        {"name": "refresh_token", "action": "refresh_credentials", "params": {}},
        {"name": "use_cached_result", "action": "cache_fallback", "params": {}},
    ],
    "timeout": [
        {"name": "retry_smaller", "action": "retry_smaller", "params": {}},
        {"name": "skip", "action": "skip", "params": {}},
    ],
    "cascade_failure": [
        {"name": "circuit_breaker", "action": "fallback", "params": {}},
        {"name": "checkpoint_rollback", "action": "checkpoint_rollback", "params": {}},
    ],
    "infinite_retry_loop": [
        {"name": "break_loop", "action": "halt_retries", "params": {}},
        {"name": "checkpoint_rollback", "action": "checkpoint_rollback", "params": {}},
    ],
    "unknown_error": [
        {"name": "checkpoint_rollback", "action": "checkpoint_rollback", "params": {}},
    ],
}

STRATEGY_NAME_TO_ACTION = {
    strategy["name"]: {"action": strategy["action"], "params": strategy.get("params", {})}
    for strategies in RECOVERY_STRATEGIES.values()
    for strategy in strategies
}


class RecoveryEngine:
    def __init__(self, detector, memory_client: HydraDBClient | None = None):
        self.detector = detector
        self.memory = memory_client or get_client()
        self.checkpoint: dict[str, Any] | None = None

    def save_checkpoint(self, state: dict[str, Any]) -> None:
        self.checkpoint = {
            "state": _copy_jsonlike(state),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def rollback(self) -> bool:
        return self.checkpoint is not None

    def get_checkpoint_state(self) -> dict[str, Any]:
        return self.checkpoint["state"] if self.checkpoint else {}

    def recover(self, agent_id: str, tool: str, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        failure_type = self.detector.diagnose(tool, error)
        context["failure_type"] = failure_type
        attempted = set(context.setdefault("attempted_strategies", []))

        past_recoveries = self.memory.find_past_recovery(f"{failure_type} on {tool}")
        for memory in past_recoveries:
            content = str(memory.get("content", ""))
            metadata = memory.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            if not self._matches_failure_type(metadata, content, failure_type):
                continue

            learned = self._parse_learned_strategy(content, metadata)
            if not learned or learned["name"] in attempted:
                continue

            context["attempted_strategies"].append(learned["name"])
            attempted.add(learned["name"])
            success = self._execute_strategy(learned, context)
            if not success:
                self.memory.log_recovery(agent_id, failure_type, learned["name"], False, tool=tool)
                continue
            if success:
                return {
                    "recovered": True,
                    "failure_type": failure_type,
                    "strategy": f"hydradb:{learned['name']}",
                    "strategy_name": learned["name"],
                    "learned": True,
                }

        for strategy in RECOVERY_STRATEGIES.get(failure_type, RECOVERY_STRATEGIES["unknown_error"]):
            if strategy["name"] in attempted:
                continue

            context["attempted_strategies"].append(strategy["name"])
            attempted.add(strategy["name"])
            success = self._execute_strategy(strategy, context)
            if not success:
                self.memory.log_recovery(agent_id, failure_type, strategy["name"], False, tool=tool)
                continue
            if success:
                return {
                    "recovered": True,
                    "failure_type": failure_type,
                    "strategy": strategy["name"],
                    "strategy_name": strategy["name"],
                    "learned": False,
                }

        rolled_back = self.rollback() and "checkpoint_rollback" not in attempted
        if rolled_back:
            context["attempted_strategies"].append("checkpoint_rollback")
        return {
            "recovered": rolled_back,
            "failure_type": failure_type,
            "strategy": "checkpoint_rollback",
            "strategy_name": "checkpoint_rollback",
            "checkpoint": rolled_back,
            "learned": False,
        }

    def _parse_learned_strategy(self, content: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
        strategy_name = metadata.get("strategy")
        if isinstance(strategy_name, str) and strategy_name in STRATEGY_NAME_TO_ACTION:
            return {"name": strategy_name, **STRATEGY_NAME_TO_ACTION[strategy_name]}

        normalized = content.lower().replace("_", " ")
        for name, action in STRATEGY_NAME_TO_ACTION.items():
            if name.replace("_", " ") in normalized:
                return {"name": name, **action}

        return None

    def _matches_failure_type(self, metadata: dict[str, Any], content: str, failure_type: str) -> bool:
        remembered_failure_type = metadata.get("failure_type")
        if isinstance(remembered_failure_type, str):
            return remembered_failure_type == failure_type

        normalized_content = content.lower().replace(" ", "_")
        for known_failure_type in KNOWN_FAILURE_TYPES:
            if known_failure_type in normalized_content:
                return known_failure_type == failure_type
        return True

    def _execute_strategy(self, strategy: dict[str, Any], context: dict[str, Any]) -> bool:
        action = strategy.get("action")
        params = strategy.get("params", {})

        if action == "wait":
            time.sleep(float(params.get("seconds", 0.2)))
            context["waited"] = True
            return True
        if action == "fallback":
            context["use_backup"] = True
            return True
        if action == "refresh_credentials":
            context["token_refreshed"] = True
            return True
        if action == "cache_fallback":
            context["use_cache"] = True
            return True
        if action == "retry_smaller":
            context["chunk_reduced"] = True
            return True
        if action == "halt_retries":
            context["retry_count"] = 0
            return True
        if action == "skip":
            context["skip_tool"] = True
            return True
        if action == "checkpoint_rollback":
            return self.rollback()

        return False


def _copy_jsonlike(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy_jsonlike(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_jsonlike(item) for item in value]
    return value
