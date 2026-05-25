from detector import FailureDetector
from hydradb_client import HydraDBClient
from recovery import RecoveryEngine


def test_recovery_uses_learned_antibody_first(tmp_path):
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    memory.log_recovery("previous-agent", "rate_limit", "exponential_backoff", True, tool="scrape_url")

    engine = RecoveryEngine(FailureDetector(), memory)
    result = engine.recover(
        agent_id="agent-1",
        tool="scrape_url",
        error=RuntimeError("429 Too Many Requests"),
        context={},
    )

    assert result["recovered"] is True
    assert result["learned"] is True
    assert result["strategy"] == "hydradb:exponential_backoff"


def test_recovery_does_not_reuse_mismatched_failure_type(tmp_path):
    memory = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    memory.log_recovery("previous-agent", "auth_failure", "refresh_token", True, tool="scrape_url")

    engine = RecoveryEngine(FailureDetector(), memory)
    context = {}
    result = engine.recover(
        agent_id="agent-1",
        tool="scrape_url",
        error=RuntimeError("503 Service Unavailable: upstream failed"),
        context=context,
    )

    assert result["recovered"] is True
    assert result["learned"] is False
    assert result["strategy"] == "circuit_breaker"
    assert context["use_backup"] is True


def test_recovery_rejects_mismatched_failure_type_in_content():
    class MemoryWithoutMetadata:
        def find_past_recovery(self, _query, max_results=3):
            return [
                {
                    "content": "Recovery for previous-agent: auth_failure resolved via refresh_token.",
                    "metadata": {},
                }
            ]

        def log_recovery(self, *args, **kwargs):
            return None

    engine = RecoveryEngine(FailureDetector(), MemoryWithoutMetadata())
    result = engine.recover(
        agent_id="agent-1",
        tool="scrape_url",
        error=RuntimeError("503 Service Unavailable: upstream failed"),
        context={},
    )

    assert result["recovered"] is True
    assert result["learned"] is False
    assert result["strategy"] == "circuit_breaker"
