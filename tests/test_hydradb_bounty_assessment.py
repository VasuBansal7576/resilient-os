import json

from hydradb_bounty_assessment import HydraDBBountyAssessment


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        response = self.responses.pop(0)
        if callable(response):
            return response(self)
        return response


def _assessment(responses, api_key="hydra-key"):
    return HydraDBBountyAssessment(
        base_url="https://api.hydradb.test",
        api_key=api_key,
        tenant_id="tenant-a",
        sub_tenant_id="resilient-os",
        session=FakeSession(responses),
    )


def test_auth_checks_report_pass_when_unauthenticated_requests_are_rejected():
    assessment = _assessment([FakeResponse(401)])

    check = assessment.check_tenant_ids_require_auth()

    assert check.status == "pass"
    assert check.evidence["status_code"] == 401


def test_auth_checks_report_finding_when_memory_write_accepts_no_auth():
    assessment = _assessment([FakeResponse(200, {"success": True})])

    check = assessment.check_memory_add_requires_auth()

    assert check.status == "finding"
    assert check.severity == "critical"


def test_tenant_create_requires_auth_passes_when_rejected():
    assessment = _assessment([FakeResponse(403)])

    check = assessment.check_tenant_create_requires_auth()

    assert check.status == "pass"


def test_processing_status_requires_auth_flags_open_status_lookup():
    assessment = _assessment([FakeResponse(200, {"statuses": []})])

    check = assessment.check_processing_status_requires_auth()

    assert check.status == "finding"
    assert check.severity == "high"


def test_malformed_json_hygiene_flags_stack_leak():
    assessment = _assessment([FakeResponse(500, text="Traceback: sqlalchemy failure")])

    check = assessment.check_malformed_json_error_hygiene()

    assert check.status == "finding"
    assert check.severity == "medium"


def test_unknown_tenant_isolation_flags_returned_items():
    assessment = _assessment([FakeResponse(200, {"chunks": [{"chunk_content": "other data"}]})])

    check = assessment.check_unknown_tenant_isolation()

    assert check.status == "finding"
    assert check.severity == "high"
    assert check.evidence["returned_items"] == 1


def test_unknown_tenant_isolation_passes_empty_response():
    assessment = _assessment([FakeResponse(200, {"chunks": [], "sources": []})])

    check = assessment.check_unknown_tenant_isolation()

    assert check.status == "pass"
    assert check.evidence["returned_items"] == 0


def test_unknown_sub_tenant_isolation_flags_returned_items():
    assessment = _assessment([FakeResponse(200, {"sources": [{"id": "memory-1"}]})])

    check = assessment.check_unknown_sub_tenant_isolation()

    assert check.status == "finding"
    assert check.severity == "high"


def test_cors_wildcard_with_credentials_is_finding():
    assessment = _assessment(
        [
            FakeResponse(
                204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Credentials": "true",
                },
            )
        ]
    )

    check = assessment.check_cors_not_wildcard_for_api()

    assert check.status == "finding"
    assert check.severity == "medium"


def test_live_write_probe_passes_when_probe_is_recalled():
    def echo_probe(session):
        probe_text = session.requests[0]["json"]["memories"][0]["text"]
        return FakeResponse(200, {"chunks": [{"chunk_content": probe_text}]}, text=probe_text)

    assessment = _assessment(
        [
            FakeResponse(200, {"success": True}),
            echo_probe,
        ]
    )

    check = assessment.check_own_tenant_add_recall_probe()

    assert check.status == "pass"
    assert assessment.session.requests[0]["json"]["tenant_id"] == "tenant-a"
    assert assessment.session.requests[1]["json"]["tenant_id"] == "tenant-a"
