from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from dotenv import load_dotenv

load_dotenv()

HYDRA_BASE = "https://api.hydradb.com"
DEFAULT_TENANT = "resilient-os"
DEFAULT_SUB_TENANT = "resilient-os"
DEFAULT_LOCAL_GRAPH = ".resilient_os/local_graph.jsonl"
KNOWN_FAILURE_TYPES = (
    "rate_limit",
    "auth_failure",
    "timeout",
    "cascade_failure",
    "infinite_retry_loop",
    "unknown_error",
)


class HydraDBClient:
    """HydraDB memory client with a durable local JSONL fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        tenant_id: str | None = None,
        sub_tenant_id: str | None = None,
        base_url: str | None = None,
        local_path: str | Path | None = None,
        online: bool | None = None,
        timeout: float = 2.0,
    ):
        self.api_key = api_key if api_key is not None else _env("HYDRADB_API_KEY", "HYDRA_KEY")
        self.tenant_id = tenant_id or _env("HYDRADB_TENANT_ID", "TENANT") or DEFAULT_TENANT
        self.sub_tenant_id = sub_tenant_id or os.getenv("HYDRADB_SUB_TENANT_ID") or DEFAULT_SUB_TENANT
        self.base_url = (base_url or os.getenv("HYDRADB_BASE_URL") or HYDRA_BASE).rstrip("/")
        self.timeout = timeout
        self.local_path = Path(local_path or os.getenv("RESILIENT_OS_LOCAL_GRAPH") or DEFAULT_LOCAL_GRAPH)
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.local_graph: list[dict[str, Any]] = self._load_local_graph()
        self.online = bool(self.api_key) and (self._check_connection() if online is None else online)
        self._tenant_ready = False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _check_connection(self) -> bool:
        try:
            response = requests.get(
                f"{self.base_url}/tenants/infra/status",
                params={"tenant_id": self.tenant_id},
                headers=self.headers,
                timeout=self.timeout,
            )
            return response.status_code in (200, 404)
        except requests.RequestException:
            return False

    def setup_tenant(self) -> bool:
        if not self.online:
            return False
        if self._tenant_ready:
            return True
        try:
            status = requests.get(
                f"{self.base_url}/tenants/infra/status",
                params={"tenant_id": self.tenant_id},
                headers=self.headers,
                timeout=self.timeout,
            )
            if status.status_code == 200:
                self._tenant_ready = True
                return True

            response = requests.post(
                f"{self.base_url}/tenants/create",
                headers=self.headers,
                json={"tenant_id": self.tenant_id},
                timeout=self.timeout,
            )
            self._tenant_ready = response.status_code in (200, 201, 202, 409)
            return self._tenant_ready
        except requests.RequestException:
            self.online = False
            return False

    def log(self, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "id": str(uuid4()),
            "content": content,
            "metadata": metadata,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self.local_graph.append(entry)
            self._append_local(entry)

        if self.online:
            threading.Thread(target=self._sync_memory, args=(content, metadata), daemon=True).start()

        return entry

    def log_tool_call(
        self,
        agent_id: str,
        tool: str,
        args: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        return self.log(
            content=f"Agent {agent_id} called {tool}. Status: {status}. Error: {error or 'none'}.",
            metadata={
                "type": "tool_call",
                "agent_id": agent_id,
                "tool": tool,
                "args": args,
                "status": status,
                "error": error,
            },
        )

    def log_recovery(
        self,
        agent_id: str,
        failure_type: str,
        strategy: str,
        success: bool,
        *,
        tool: str | None = None,
        verified: bool = True,
    ) -> dict[str, Any]:
        outcome = "SUCCESS" if success else "FAILED"
        metadata = {
            "type": "recovery",
            "agent_id": agent_id,
            "failure_type": failure_type,
            "strategy": strategy,
            "success": success,
            "verified": verified,
        }
        if tool:
            metadata["tool"] = tool

        return self.log(
            content=f"Recovery for {agent_id}: {failure_type} resolved via {strategy}. Outcome: {outcome}.",
            metadata=metadata,
        )

    def find_past_recovery(self, failure_description: str, max_results: int = 3) -> list[dict[str, Any]]:
        return self.query(failure_description, max_results=max_results)

    def query(self, query: str, max_results: int = 3) -> list[dict[str, Any]]:
        if self.online:
            try:
                response = requests.post(
                    f"{self.base_url}/recall/recall_preferences",
                    headers=self.headers,
                    json={
                        "tenant_id": self.tenant_id,
                        "sub_tenant_id": self.sub_tenant_id,
                        "query": query,
                        "max_results": max_results,
                        "graph_context": True,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                results = self._extract_results(response.json())
                if results:
                    return results[:max_results]
            except (requests.RequestException, ValueError):
                self.online = False

        return self._local_recovery_search(query, max_results)

    def clear_local_graph(self) -> None:
        with self._lock:
            self.local_graph.clear()
            self.local_path.parent.mkdir(parents=True, exist_ok=True)
            self.local_path.write_text("", encoding="utf-8")

    def _sync_memory(self, content: str, metadata: dict[str, Any]) -> None:
        try:
            if not self.setup_tenant():
                self.online = False
                return
            response = requests.post(
                f"{self.base_url}/memories/add_memory",
                headers=self.headers,
                json={
                    "tenant_id": self.tenant_id,
                    "sub_tenant_id": self.sub_tenant_id,
                    "memories": [
                        {
                            "text": content,
                            "infer": False,
                            "metadata": metadata,
                        }
                    ],
                },
                timeout=5,
            )
            response.raise_for_status()
        except requests.RequestException:
            self.online = False

    def _append_local(self, entry: dict[str, Any]) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        with self.local_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str, sort_keys=True) + "\n")

    def _load_local_graph(self) -> list[dict[str, Any]]:
        if not self.local_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in self.local_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    entries.append(item)
            except json.JSONDecodeError:
                continue
        return entries

    def _extract_results(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [
                result
                for item in payload
                if isinstance(item, dict)
                for result in [self._normalize_result(item)]
                if result["content"] or result["metadata"]
            ]
        if not isinstance(payload, dict):
            return []

        for key in ("results", "memories", "matches", "items", "documents", "chunks", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    result
                    for item in value
                    if isinstance(item, dict)
                    for result in [self._normalize_result(item)]
                    if result["content"] or result["metadata"]
                ]
            if isinstance(value, dict):
                nested = self._extract_results(value)
                if nested:
                    return nested
        normalized = self._normalize_result(payload)
        if normalized["content"] or normalized["metadata"]:
            return [normalized]
        return []

    def _normalize_result(self, item: dict[str, Any]) -> dict[str, Any]:
        memory: dict[str, Any] = item
        content_override = ""
        for key in ("memory", "document", "node", "record", "payload", "item"):
            value = item.get(key)
            if isinstance(value, dict):
                memory = value
                break
            if isinstance(value, str):
                content_override = value
                break

        content = (
            content_override
            or _first_text(memory, "content", "text", "document", "body", "page_content", "chunk_content")
            or _first_text(item, "content", "text", "document", "body", "page_content", "chunk_content")
            or ""
        )
        metadata = _coerce_metadata(
            memory.get("metadata")
            or memory.get("document_metadata")
            or memory.get("tenant_metadata")
            or memory.get("additional_metadata")
            or memory.get("meta")
            or memory.get("properties")
            or item.get("metadata")
            or item.get("document_metadata")
            or item.get("tenant_metadata")
            or item.get("additional_metadata")
            or item.get("meta")
            or item.get("properties")
        )

        normalized: dict[str, Any] = {"content": content, "metadata": metadata, "raw": item}
        for key in ("id", "score", "relevancy_score", "timestamp", "source_id", "chunk_uuid"):
            if key in item:
                normalized[key] = item[key]
            elif key in memory:
                normalized[key] = memory[key]
        return normalized

    def _local_recovery_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        expected_failure_type = _failure_type_from_query(query)
        latest_failures: dict[tuple[str, str, str], str] = {}
        scored: list[tuple[int, str, dict[str, Any]]] = []

        for entry in self.local_graph:
            metadata = entry.get("metadata", {})
            if metadata.get("type") != "recovery" or metadata.get("success") is not False:
                continue
            key = _recovery_key(metadata)
            latest_failures[key] = max(latest_failures.get(key, ""), str(entry.get("timestamp", "")))

        for entry in self.local_graph:
            metadata = entry.get("metadata", {})
            if (
                metadata.get("type") != "recovery"
                or metadata.get("success") is not True
                or metadata.get("verified") is False
            ):
                continue
            if expected_failure_type and metadata.get("failure_type") != expected_failure_type:
                continue

            timestamp = str(entry.get("timestamp", ""))
            failed_at = latest_failures.get(_recovery_key(metadata), "")
            if failed_at and failed_at >= timestamp:
                continue

            haystack = " ".join(
                [
                    str(entry.get("content", "")),
                    str(metadata.get("failure_type", "")),
                    str(metadata.get("strategy", "")),
                    str(metadata.get("tool", "")),
                ]
            ).lower().replace("_", " ")
            score = sum(1 for token in tokens if token in haystack)
            if expected_failure_type and metadata.get("failure_type") == expected_failure_type:
                score += 10
            if score:
                scored.append((score, timestamp, entry))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for _, _, entry in scored[:max_results]]


_client: HydraDBClient | None = None


def get_client() -> HydraDBClient:
    global _client
    if _client is None:
        _client = HydraDBClient()
        _client.setup_tenant()
    return _client


def reset_client(client: HydraDBClient | None = None) -> None:
    global _client
    _client = client


def log_tool_call(agent_id: str, tool: str, args: dict[str, Any], status: str, error: str | None = None):
    return get_client().log_tool_call(agent_id, tool, args, status, error)


def log_recovery(agent_id: str, failure_type: str, strategy: str, success: bool):
    return get_client().log_recovery(agent_id, failure_type, strategy, success)


def find_past_recovery(failure_description: str) -> list[dict[str, Any]]:
    return get_client().find_past_recovery(failure_description)


def get_local_graph() -> list[dict[str, Any]]:
    return get_client().local_graph


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _first_text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            return value
    return None


def _coerce_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")) if len(token) > 2}


def _failure_type_from_query(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    for failure_type in KNOWN_FAILURE_TYPES:
        if failure_type in normalized:
            return failure_type
    return None


def _recovery_key(metadata: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(metadata.get("failure_type", "")),
        str(metadata.get("strategy", "")),
        str(metadata.get("tool", "")),
    )
