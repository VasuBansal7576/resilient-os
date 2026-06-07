from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import requests
from dotenv import load_dotenv


load_dotenv()

DEFAULT_BASE_URL = "https://api.hydradb.com"
AUTH_FAILURE_STATUSES = {401, 403}
SAFE_FAILURE_STATUSES = {400, 401, 403, 404, 405, 422}
LEAK_PATTERNS = (
    re.compile(r"traceback", re.IGNORECASE),
    re.compile(r"stack trace", re.IGNORECASE),
    re.compile(r"sqlalchemy|psycopg|postgres|mongodb|redis", re.IGNORECASE),
    re.compile(r"aws_secret|secret_access_key|private key", re.IGNORECASE),
    re.compile(r"bearer\s+[a-z0-9._-]{12,}", re.IGNORECASE),
)


@dataclass(frozen=True)
class BountyCheck:
    name: str
    status: str
    severity: str
    target: str
    detail: str
    evidence: dict[str, Any]


class HydraDBBountyAssessment:
    """Low-impact authorized checks for the HydraDB public API.

    The harness never brute forces, never enumerates customer data, and only
    writes a single probe memory when --live-write is explicitly enabled.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        tenant_id: str,
        sub_tenant_id: str,
        session: Any | None = None,
        timeout: float = 8.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.sub_tenant_id = sub_tenant_id
        self.session = session or requests.Session()
        self.timeout = timeout

    def run(self, *, live_write: bool = False) -> list[BountyCheck]:
        checks = [
            self.check_tenant_ids_require_auth(),
            self.check_tenant_create_requires_auth(),
            self.check_memory_add_requires_auth(),
            self.check_processing_status_requires_auth(),
            self.check_invalid_token_rejected(),
            self.check_malformed_json_error_hygiene(),
            self.check_unknown_tenant_isolation(),
            self.check_unknown_sub_tenant_isolation(),
            self.check_cors_not_wildcard_for_api(),
        ]
        if live_write:
            checks.append(self.check_own_tenant_add_recall_probe())
        return checks

    def check_tenant_ids_require_auth(self) -> BountyCheck:
        response = self._request("GET", "/tenants/tenant_ids", auth="none")
        if response.status_code in AUTH_FAILURE_STATUSES:
            return self._pass("tenant_ids_require_auth", "/tenants/tenant_ids", response, "Unauthenticated tenant list was rejected.")
        return self._finding(
            "tenant_ids_require_auth",
            "high",
            "/tenants/tenant_ids",
            response,
            "Unauthenticated tenant list did not return 401/403.",
        )

    def check_memory_add_requires_auth(self) -> BountyCheck:
        response = self._request(
            "POST",
            "/memories/add_memory",
            auth="none",
            json_body={
                "tenant_id": self.tenant_id,
                "sub_tenant_id": self.sub_tenant_id,
                "memories": [{"text": "unauthenticated probe", "infer": False}],
            },
        )
        if response.status_code in AUTH_FAILURE_STATUSES:
            return self._pass("memory_add_requires_auth", "/memories/add_memory", response, "Unauthenticated memory write was rejected.")
        return self._finding(
            "memory_add_requires_auth",
            "critical",
            "/memories/add_memory",
            response,
            "Unauthenticated memory write did not return 401/403.",
        )

    def check_tenant_create_requires_auth(self) -> BountyCheck:
        response = self._request(
            "POST",
            "/tenants/create",
            auth="none",
            json_body={"tenant_id": f"resilientos-unauth-create-{uuid4().hex[:12]}"},
        )
        if response.status_code in AUTH_FAILURE_STATUSES:
            return self._pass("tenant_create_requires_auth", "/tenants/create", response, "Unauthenticated tenant creation was rejected.")
        return self._finding(
            "tenant_create_requires_auth",
            "critical",
            "/tenants/create",
            response,
            "Unauthenticated tenant creation did not return 401/403.",
        )

    def check_processing_status_requires_auth(self) -> BountyCheck:
        response = self._request(
            "POST",
            f"/ingestion/verify_processing?file_ids=resilientos-fake-file&tenant_id={self.tenant_id}",
            auth="none",
        )
        if response.status_code in AUTH_FAILURE_STATUSES:
            return self._pass(
                "processing_status_requires_auth",
                "/ingestion/verify_processing",
                response,
                "Unauthenticated processing-status lookup was rejected.",
            )
        return self._finding(
            "processing_status_requires_auth",
            "high",
            "/ingestion/verify_processing",
            response,
            "Unauthenticated processing-status lookup did not return 401/403.",
        )

    def check_invalid_token_rejected(self) -> BountyCheck:
        response = self._request("GET", "/tenants/tenant_ids", auth="invalid")
        if response.status_code in AUTH_FAILURE_STATUSES:
            return self._pass("invalid_token_rejected", "/tenants/tenant_ids", response, "Invalid bearer token was rejected.")
        return self._finding(
            "invalid_token_rejected",
            "critical",
            "/tenants/tenant_ids",
            response,
            "Invalid bearer token did not return 401/403.",
        )

    def check_malformed_json_error_hygiene(self) -> BountyCheck:
        if not self.api_key:
            return self._skipped("malformed_json_error_hygiene", "/recall/recall_preferences", "HYDRADB_API_KEY is required.")

        response = self._request(
            "POST",
            "/recall/recall_preferences",
            auth="valid",
            raw_body="{not-json",
            content_type="application/json",
        )
        body = _safe_body(response)
        if _has_internal_leak(body):
            return self._finding(
                "malformed_json_error_hygiene",
                "medium",
                "/recall/recall_preferences",
                response,
                "Malformed JSON response appears to leak stack/internal details.",
            )
        if response.status_code in SAFE_FAILURE_STATUSES:
            return self._pass(
                "malformed_json_error_hygiene",
                "/recall/recall_preferences",
                response,
                "Malformed JSON failed closed without obvious internal leakage.",
            )
        return self._review(
            "malformed_json_error_hygiene",
            "low",
            "/recall/recall_preferences",
            response,
            "Malformed JSON returned an unexpected status; review manually.",
        )

    def check_unknown_tenant_isolation(self) -> BountyCheck:
        if not self.api_key:
            return self._skipped("unknown_tenant_isolation", "/recall/recall_preferences", "HYDRADB_API_KEY is required.")

        unknown_tenant = f"resilientos-unauthorized-{uuid4().hex[:12]}"
        response = self._request(
            "POST",
            "/recall/recall_preferences",
            auth="valid",
            json_body={
                "tenant_id": unknown_tenant,
                "sub_tenant_id": self.sub_tenant_id,
                "query": "rate limit recovery",
                "max_results": 3,
                "graph_context": True,
            },
        )
        payload = _json_or_empty(response)
        returned_items = _count_returned_items(payload)
        if returned_items > 0:
            return self._finding(
                "unknown_tenant_isolation",
                "high",
                "/recall/recall_preferences",
                response,
                f"Recall for an unowned random tenant returned {returned_items} item(s).",
                extra={"unknown_tenant": unknown_tenant, "returned_items": returned_items},
            )
        if response.status_code in SAFE_FAILURE_STATUSES or response.status_code == 200:
            return self._pass(
                "unknown_tenant_isolation",
                "/recall/recall_preferences",
                response,
                "Unknown random tenant did not return recall data.",
                extra={"unknown_tenant": unknown_tenant, "returned_items": returned_items},
            )
        return self._review(
            "unknown_tenant_isolation",
            "medium",
            "/recall/recall_preferences",
            response,
            "Unknown random tenant returned an unexpected status; review manually.",
            extra={"unknown_tenant": unknown_tenant},
        )

    def check_unknown_sub_tenant_isolation(self) -> BountyCheck:
        if not self.api_key:
            return self._skipped("unknown_sub_tenant_isolation", "/recall/recall_preferences", "HYDRADB_API_KEY is required.")

        unknown_sub_tenant = f"resilientos-unknown-subtenant-{uuid4().hex[:12]}"
        response = self._request(
            "POST",
            "/recall/recall_preferences",
            auth="valid",
            json_body={
                "tenant_id": self.tenant_id,
                "sub_tenant_id": unknown_sub_tenant,
                "query": "rate limit recovery",
                "max_results": 3,
                "graph_context": True,
            },
        )
        payload = _json_or_empty(response)
        returned_items = _count_returned_items(payload)
        if returned_items > 0:
            return self._finding(
                "unknown_sub_tenant_isolation",
                "high",
                "/recall/recall_preferences",
                response,
                f"Recall for a random sub-tenant returned {returned_items} item(s).",
                extra={"unknown_sub_tenant": unknown_sub_tenant, "returned_items": returned_items},
            )
        if response.status_code in SAFE_FAILURE_STATUSES or response.status_code == 200:
            return self._pass(
                "unknown_sub_tenant_isolation",
                "/recall/recall_preferences",
                response,
                "Unknown random sub-tenant did not return recall data.",
                extra={"unknown_sub_tenant": unknown_sub_tenant, "returned_items": returned_items},
            )
        return self._review(
            "unknown_sub_tenant_isolation",
            "medium",
            "/recall/recall_preferences",
            response,
            "Unknown random sub-tenant returned an unexpected status; review manually.",
            extra={"unknown_sub_tenant": unknown_sub_tenant},
        )

    def check_cors_not_wildcard_for_api(self) -> BountyCheck:
        response = self._request(
            "OPTIONS",
            "/memories/add_memory",
            auth="none",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        allow_origin = response.headers.get("Access-Control-Allow-Origin", "")
        allow_credentials = response.headers.get("Access-Control-Allow-Credentials", "")
        if allow_origin == "*" and allow_credentials.lower() == "true":
            return self._finding(
                "cors_not_wildcard_for_api",
                "medium",
                "/memories/add_memory",
                response,
                "CORS allows wildcard origin with credentials on an authenticated API path.",
                extra={"access_control_allow_origin": allow_origin, "access_control_allow_credentials": allow_credentials},
            )
        return self._pass(
            "cors_not_wildcard_for_api",
            "/memories/add_memory",
            response,
            "No wildcard-with-credentials CORS posture observed.",
            extra={"access_control_allow_origin": allow_origin, "access_control_allow_credentials": allow_credentials},
        )

    def check_own_tenant_add_recall_probe(self) -> BountyCheck:
        if not self.api_key:
            return self._skipped("own_tenant_add_recall_probe", "/memories/add_memory", "HYDRADB_API_KEY is required.")

        probe_id = f"resilientos-bounty-probe-{uuid4().hex}"
        add_response = self._request(
            "POST",
            "/memories/add_memory",
            auth="valid",
            json_body={
                "tenant_id": self.tenant_id,
                "sub_tenant_id": self.sub_tenant_id,
                "memories": [
                    {
                        "text": f"{probe_id}: benign authorized security probe for own tenant recall.",
                        "infer": False,
                        "metadata": {"type": "security_probe", "probe_id": probe_id},
                    }
                ],
            },
        )
        if add_response.status_code >= 400:
            return self._review(
                "own_tenant_add_recall_probe",
                "low",
                "/memories/add_memory",
                add_response,
                "Own-tenant add-memory probe failed; review configuration or API status.",
                extra={"probe_id": probe_id},
            )

        recall_response = self._request(
            "POST",
            "/recall/recall_preferences",
            auth="valid",
            json_body={
                "tenant_id": self.tenant_id,
                "sub_tenant_id": self.sub_tenant_id,
                "query": probe_id,
                "max_results": 3,
                "graph_context": True,
            },
        )
        payload = _json_or_empty(recall_response)
        body = _safe_body(recall_response)
        probe_seen = probe_id in body
        if recall_response.status_code < 400 and probe_seen:
            return self._pass(
                "own_tenant_add_recall_probe",
                "/memories/add_memory + /recall/recall_preferences",
                recall_response,
                "Own-tenant add and recall worked for the benign probe.",
                extra={"probe_id": probe_id, "payload_keys": sorted(payload) if isinstance(payload, dict) else []},
            )
        if "resilientos-bounty-probe-" in body:
            return self._review(
                "own_tenant_add_recall_probe",
                "low",
                "/memories/add_memory + /recall/recall_preferences",
                recall_response,
                "Own-tenant probe was added, but exact probe recall returned a different previously indexed probe.",
                extra={"probe_id": probe_id, "payload_keys": sorted(payload) if isinstance(payload, dict) else []},
            )
        return self._review(
            "own_tenant_add_recall_probe",
            "low",
            "/memories/add_memory + /recall/recall_preferences",
            recall_response,
            "Own-tenant probe was added but not immediately recalled; ingestion may be asynchronous.",
            extra={"probe_id": probe_id, "payload_keys": sorted(payload) if isinstance(payload, dict) else []},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: str,
        json_body: Any | None = None,
        raw_body: str | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        request_headers = {"Content-Type": content_type, **(headers or {})}
        if auth == "valid" and self.api_key:
            request_headers["Authorization"] = f"Bearer {self.api_key}"
        elif auth == "invalid":
            request_headers["Authorization"] = "Bearer hydradb-invalid-token-for-authorized-security-test"

        kwargs: dict[str, Any] = {"headers": request_headers, "timeout": self.timeout}
        if json_body is not None:
            kwargs["json"] = json_body
        if raw_body is not None:
            kwargs["data"] = raw_body

        return self.session.request(method, f"{self.base_url}{path}", **kwargs)

    def _pass(
        self,
        name: str,
        target: str,
        response: requests.Response,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> BountyCheck:
        return _check(name, "pass", "info", target, response, detail, extra)

    def _finding(
        self,
        name: str,
        severity: str,
        target: str,
        response: requests.Response,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> BountyCheck:
        return _check(name, "finding", severity, target, response, detail, extra)

    def _review(
        self,
        name: str,
        severity: str,
        target: str,
        response: requests.Response,
        detail: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> BountyCheck:
        return _check(name, "manual_review", severity, target, response, detail, extra)

    def _skipped(self, name: str, target: str, detail: str) -> BountyCheck:
        return BountyCheck(name, "skipped", "info", target, detail, {"reason": detail})


def _check(
    name: str,
    status: str,
    severity: str,
    target: str,
    response: requests.Response,
    detail: str,
    extra: dict[str, Any] | None,
) -> BountyCheck:
    evidence = {
        "status_code": response.status_code,
        "body_excerpt": _safe_body(response)[:500],
    }
    if extra:
        evidence.update(extra)
    return BountyCheck(name, status, severity, target, detail, evidence)


def _json_or_empty(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _safe_body(response: requests.Response) -> str:
    text = response.text or ""
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
    text = re.sub(r"gh[opsu]_[A-Za-z0-9_]+", "<redacted-token>", text)
    return text


def _has_internal_leak(body: str) -> bool:
    return any(pattern.search(body) for pattern in LEAK_PATTERNS)


def _count_returned_items(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    count = 0
    for key in ("chunks", "sources", "results", "memories", "matches", "items", "documents"):
        value = payload.get(key)
        if isinstance(value, list):
            count += len(value)
    data = payload.get("data")
    if isinstance(data, dict):
        count += _count_returned_items(data)
    return count


def _summarize(checks: list[BountyCheck]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "HydraDB public API, low-impact authorized checks only",
        "counts": counts,
        "findings": [asdict(check) for check in checks if check.status == "finding"],
        "manual_review": [asdict(check) for check in checks if check.status == "manual_review"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run low-impact authorized HydraDB bounty checks.")
    parser.add_argument("--base-url", default=os.getenv("HYDRADB_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--tenant-id", default=os.getenv("HYDRADB_TENANT_ID", "resilient-os"))
    parser.add_argument("--sub-tenant-id", default=os.getenv("HYDRADB_SUB_TENANT_ID", "resilient-os"))
    parser.add_argument("--live-write", action="store_true", help="Write one benign probe memory to your configured tenant.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    assessment = HydraDBBountyAssessment(
        base_url=args.base_url,
        api_key=os.getenv("HYDRADB_API_KEY") or os.getenv("HYDRA_KEY"),
        tenant_id=args.tenant_id,
        sub_tenant_id=args.sub_tenant_id,
    )
    checks = assessment.run(live_write=args.live_write)
    payload = {
        "summary": _summarize(checks),
        "checks": [asdict(check) for check in checks],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for check in checks:
            print(f"{check.status.upper()} {check.name} [{check.severity}]: {check.detail}")
        print(json.dumps(payload["summary"], indent=2))

    if any(check.status == "finding" and check.severity in {"critical", "high"} for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
