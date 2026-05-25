from __future__ import annotations

import json

import pytest

from integrations import (
    IntegrationError,
    IntegrationNotConfigured,
    LLMProviderChain,
    RealToolRunner,
    ToolCache,
)


@pytest.fixture(autouse=True)
def clean_integration_env(monkeypatch):
    for key in [
        "DISCORD_WEBHOOK_URL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "LLM_PROVIDER_ORDER",
        "NVIDIA_API_KEY",
        "NVIDIA_BASE_URL",
        "NVIDIA_MODEL",
        "RESILIENT_OS_TOOL_CACHE",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_llm_provider_chain_uses_groq_primary_and_nvidia_fallback(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")

    providers = LLMProviderChain().configured_providers()

    assert [provider.name for provider in providers] == ["groq", "nvidia"]
    assert providers[0].scrapegraph_llm_config() == {
        "model": "groq/llama-3.3-70b-versatile",
        "api_key": "groq-key",
        "temperature": 0,
    }
    assert providers[1].scrapegraph_llm_config() == {
        "model": "openai/meta/llama-3.3-70b-instruct",
        "api_key": "nvidia-key",
        "temperature": 0,
        "base_url": "https://integrate.api.nvidia.com/v1",
    }


def test_scrape_url_falls_back_from_groq_to_nvidia_and_caches_result(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    cache = ToolCache(tmp_path / "tool_cache.jsonl")
    runner = RealToolRunner(cache=cache)
    attempts = []

    def fake_run_scrapegraph(url, prompt, provider):
        attempts.append((url, prompt, provider.name))
        if provider.name == "groq":
            raise RuntimeError("primary unavailable")
        return {"url": url, "facts": ["fallback worked"], "provider": provider.name}

    monkeypatch.setattr(runner, "_run_scrapegraph", fake_run_scrapegraph)

    result = runner.scrape_url({"url": "https://example.com/pricing", "prompt": "Extract prices"}, {})

    assert attempts == [
        ("https://example.com/pricing", "Extract prices", "groq"),
        ("https://example.com/pricing", "Extract prices", "nvidia"),
    ]
    assert result.startswith("ScrapeGraphAI[nvidia] extracted from https://example.com/pricing:")
    assert cache.latest("scrape_url", "https://example.com/pricing") == {
        "url": "https://example.com/pricing",
        "facts": ["fallback worked"],
        "provider": "nvidia",
    }


def test_scrape_url_cache_mode_uses_cached_result_without_provider_configuration(tmp_path):
    cache = ToolCache(tmp_path / "tool_cache.jsonl")
    cache.write("scrape_url", "https://example.com/pricing", {"price": "$99"})
    runner = RealToolRunner(cache=cache)

    result = runner.scrape_url({"url": "https://example.com/pricing"}, {"use_backup": True})

    assert result == 'ScrapeGraphAI cache result for https://example.com/pricing: {"price": "$99"}'


def test_scrape_url_cache_mode_fails_when_no_cached_result_exists(tmp_path):
    runner = RealToolRunner(cache=ToolCache(tmp_path / "tool_cache.jsonl"))

    with pytest.raises(IntegrationError, match="No cached scrape result available"):
        runner.scrape_url({"url": "https://example.com/missing"}, {"use_cache": True})


def test_scrape_url_requires_a_configured_real_llm_provider(tmp_path):
    runner = RealToolRunner(cache=ToolCache(tmp_path / "tool_cache.jsonl"))

    with pytest.raises(IntegrationNotConfigured, match="Set GROQ_API_KEY or NVIDIA_API_KEY"):
        runner.scrape_url({"url": "https://example.com/pricing"}, {})


def test_send_notification_prefers_telegram_and_shapes_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-123")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    posts = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("integrations.requests.post", fake_post)
    runner = RealToolRunner(cache=ToolCache(tmp_path / "tool_cache.jsonl"), timeout=3.5)

    result = runner.send_notification(
        {"message": "Done", "include_last_result": True},
        {"last_result": "Scrape result"},
    )

    assert result == "Telegram notification sent to chat chat-123."
    assert posts == [
        {
            "url": "https://api.telegram.org/bottelegram-token/sendMessage",
            "json": {"chat_id": "chat-123", "text": "Done\n\nScrape result"},
            "timeout": 3.5,
        }
    ]


def test_send_notification_uses_discord_when_telegram_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    posts = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("integrations.requests.post", fake_post)
    runner = RealToolRunner(cache=ToolCache(tmp_path / "tool_cache.jsonl"), timeout=2.0)

    result = runner.send_notification({}, {"last_result": "Fallback message"})

    assert result == "Discord notification sent."
    assert posts == [
        {
            "url": "https://discord.example/webhook",
            "json": {"content": "Fallback message"},
            "timeout": 2.0,
        }
    ]


def test_tool_cache_returns_latest_matching_entry_and_ignores_bad_lines(tmp_path):
    cache_path = tmp_path / "tool_cache.jsonl"
    cache_path.write_text(
        "\n".join(
            [
                json.dumps({"tool": "scrape_url", "key": "https://example.com", "result": {"version": 1}}),
                "{not-json",
                json.dumps({"tool": "send_notification", "key": "https://example.com", "result": {"wrong": True}}),
                json.dumps({"tool": "scrape_url", "key": "https://example.com", "result": {"version": 2}}),
            ]
        ),
        encoding="utf-8",
    )
    cache = ToolCache(cache_path)

    assert cache.latest("scrape_url", "https://example.com") == {"version": 2}
