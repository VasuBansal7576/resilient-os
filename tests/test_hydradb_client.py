from hydradb_client import HydraDBClient


def test_local_recovery_search_and_persistence(tmp_path):
    graph_path = tmp_path / "graph.jsonl"
    client = HydraDBClient(api_key="", local_path=graph_path, online=False)

    client.log_recovery("agent-1", "rate_limit", "exponential_backoff", True, tool="scrape_url")

    results = client.find_past_recovery("rate limit on scrape_url")
    assert len(results) == 1
    assert results[0]["metadata"]["strategy"] == "exponential_backoff"

    reloaded = HydraDBClient(api_key="", local_path=graph_path, online=False)
    assert len(reloaded.local_graph) == 1


def test_local_search_requires_matching_failure_type(tmp_path):
    client = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    client.log_recovery("agent-1", "auth_failure", "refresh_token", True, tool="scrape_url")
    client.log_recovery("agent-1", "rate_limit", "exponential_backoff", True, tool="scrape_url")

    assert client.find_past_recovery("cascade_failure on scrape_url") == []

    client.log_recovery("agent-1", "cascade_failure", "circuit_breaker", True, tool="scrape_url")
    results = client.find_past_recovery("cascade_failure on scrape_url")

    assert len(results) == 1
    assert results[0]["metadata"]["strategy"] == "circuit_breaker"


def test_local_search_ignores_strategy_with_newer_failure(tmp_path):
    client = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    client.log_recovery("agent-1", "rate_limit", "exponential_backoff", True, tool="scrape_url")
    client.log_recovery("agent-1", "rate_limit", "exponential_backoff", False, tool="scrape_url")

    assert client.find_past_recovery("rate_limit on scrape_url") == []


def test_hydradb_response_normalization_handles_nested_shapes(tmp_path):
    client = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    payload = {
        "data": {
            "matches": [
                {
                    "memory": {
                        "text": "Recovery for agent-1: rate_limit resolved via switch_to_backup.",
                        "metadata": '{"failure_type": "rate_limit", "strategy": "switch_to_backup"}',
                    },
                    "score": 0.91,
                }
            ]
        }
    }

    results = client._extract_results(payload)

    assert len(results) == 1
    assert results[0]["content"].startswith("Recovery for agent-1")
    assert results[0]["metadata"]["strategy"] == "switch_to_backup"
    assert results[0]["score"] == 0.91


def test_hydradb_response_normalization_handles_current_recall_chunks(tmp_path):
    client = HydraDBClient(api_key="", local_path=tmp_path / "graph.jsonl", online=False)
    payload = {
        "chunks": [
            {
                "chunk_uuid": "chunk-1",
                "source_id": "memory-1",
                "chunk_content": "Recovery for agent-1: rate_limit resolved via exponential_backoff.",
                "relevancy_score": 0.88,
                "document_metadata": {"failure_type": "rate_limit", "strategy": "exponential_backoff"},
            }
        ],
        "graph_context": {"query_paths": []},
    }

    results = client._extract_results(payload)

    assert len(results) == 1
    assert results[0]["content"].startswith("Recovery for agent-1")
    assert results[0]["metadata"]["strategy"] == "exponential_backoff"
    assert results[0]["relevancy_score"] == 0.88


def test_sync_memory_uses_current_hydradb_memory_endpoint(tmp_path, monkeypatch):
    posts = []
    gets = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True}

    def fake_get(url, params, headers, timeout):
        gets.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse()

    def fake_post(url, headers, json, timeout):
        posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("hydradb_client.requests.get", fake_get)
    monkeypatch.setattr("hydradb_client.requests.post", fake_post)

    client = HydraDBClient(
        api_key="hydra-key",
        tenant_id="existing-tenant",
        sub_tenant_id="resilient-os",
        local_path=tmp_path / "graph.jsonl",
        online=True,
    )
    client._sync_memory("test memory", {"type": "tool_call", "tool": "scrape_url"})

    assert gets[0]["params"] == {"tenant_id": "existing-tenant"}
    assert posts == [
        {
            "url": "https://api.hydradb.com/memories/add_memory",
            "json": {
                "tenant_id": "existing-tenant",
                "sub_tenant_id": "resilient-os",
                "memories": [
                    {
                        "text": "test memory",
                        "infer": False,
                        "metadata": {"type": "tool_call", "tool": "scrape_url"},
                    }
                ],
            },
            "headers": {"Authorization": "Bearer hydra-key", "Content-Type": "application/json"},
            "timeout": 5,
        }
    ]


def test_query_uses_current_hydradb_memory_recall_endpoint(tmp_path, monkeypatch):
    posts = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chunks": [
                    {
                        "chunk_content": "Recovery for agent-1: timeout resolved via retry_with_jitter.",
                        "document_metadata": {"failure_type": "timeout", "strategy": "retry_with_jitter"},
                    }
                ]
            }

    def fake_post(url, headers, json, timeout):
        posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("hydradb_client.requests.post", fake_post)
    client = HydraDBClient(
        api_key="hydra-key",
        tenant_id="existing-tenant",
        sub_tenant_id="resilient-os",
        local_path=tmp_path / "graph.jsonl",
        online=True,
    )

    results = client.query("timeout on scrape_url")

    assert posts == [
        {
            "url": "https://api.hydradb.com/recall/recall_preferences",
            "json": {
                "tenant_id": "existing-tenant",
                "sub_tenant_id": "resilient-os",
                "query": "timeout on scrape_url",
                "max_results": 3,
                "graph_context": True,
            },
            "headers": {"Authorization": "Bearer hydra-key", "Content-Type": "application/json"},
            "timeout": 2.0,
        }
    ]
    assert results[0]["metadata"]["strategy"] == "retry_with_jitter"
