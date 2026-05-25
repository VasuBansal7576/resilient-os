from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SCRAPE_PROMPT = (
    "Extract the useful facts, prices, plan names, product names, dates, and links from this page. "
    "Return concise structured JSON."
)
DEFAULT_CACHE_PATH = ".resilient_os/tool_cache.jsonl"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


class IntegrationError(RuntimeError):
    """Base error for real integration failures."""


class IntegrationNotConfigured(IntegrationError):
    """Raised when a real integration is requested without required keys/config."""


@dataclass(frozen=True)
class LLMProviderConfig:
    name: str
    model: str
    api_key: str
    base_url: str | None = None

    def scrapegraph_llm_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "model": self.model,
            "api_key": self.api_key,
            "temperature": 0,
        }
        if self.base_url:
            config["base_url"] = self.base_url
        return config


class LLMProviderChain:
    def __init__(self, order: list[str] | None = None):
        raw_order = order or [
            item.strip().lower()
            for item in os.getenv("LLM_PROVIDER_ORDER", "groq,nvidia").split(",")
            if item.strip()
        ]
        self.order = raw_order

    def configured_providers(self) -> list[LLMProviderConfig]:
        providers: list[LLMProviderConfig] = []
        for name in self.order:
            if name == "groq" and os.getenv("GROQ_API_KEY"):
                providers.append(
                    LLMProviderConfig(
                        name="groq",
                        model=os.getenv("GROQ_MODEL", "groq/llama-3.3-70b-versatile"),
                        api_key=os.environ["GROQ_API_KEY"],
                    )
                )
            elif name == "nvidia" and os.getenv("NVIDIA_API_KEY"):
                providers.append(
                    LLMProviderConfig(
                        name="nvidia",
                        model=os.getenv("NVIDIA_MODEL", "openai/meta/llama-3.3-70b-instruct"),
                        api_key=os.environ["NVIDIA_API_KEY"],
                        base_url=os.getenv("NVIDIA_BASE_URL", NVIDIA_BASE_URL),
                    )
                )
        return providers

    def require_any(self) -> list[LLMProviderConfig]:
        providers = self.configured_providers()
        if not providers:
            raise IntegrationNotConfigured("No LLM provider configured. Set GROQ_API_KEY or NVIDIA_API_KEY.")
        return providers


class ToolCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("RESILIENT_OS_TOOL_CACHE", DEFAULT_CACHE_PATH))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, tool: str, key: str, result: Any) -> None:
        entry = {"tool": tool, "key": key, "result": result}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str, sort_keys=True) + "\n")

    def latest(self, tool: str, key: str) -> Any | None:
        if not self.path.exists():
            return None
        latest_result = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("tool") == tool and entry.get("key") == key:
                latest_result = entry.get("result")
        return latest_result


class RealToolRunner:
    """Runs real-world integrations. No pretend tool successes."""

    def __init__(
        self,
        llm_chain: LLMProviderChain | None = None,
        cache: ToolCache | None = None,
        timeout: float = 20.0,
    ):
        self.llm_chain = llm_chain or LLMProviderChain()
        self.cache = cache or ToolCache()
        self.timeout = timeout

    def call(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> str:
        context = context or {}
        if name == "scrape_url":
            return self.scrape_url(args, context)
        if name == "send_notification":
            return self.send_notification(args, context)
        raise ValueError(f"Unknown tool: {name}")

    def scrape_url(self, args: dict[str, Any], context: dict[str, Any]) -> str:
        url = _required(args, "url")
        prompt = str(args.get("prompt") or DEFAULT_SCRAPE_PROMPT)

        if context.get("use_backup") or context.get("use_cache"):
            cached = self.cache.latest("scrape_url", url)
            if cached is None:
                raise IntegrationError(f"No cached scrape result available for {url}")
            return f"ScrapeGraphAI cache result for {url}: {json.dumps(cached, default=str)}"

        providers = self.llm_chain.require_any()
        last_error: Exception | None = None
        for provider in providers:
            try:
                result = self._run_scrapegraph(url=url, prompt=prompt, provider=provider)
                self.cache.write("scrape_url", url, result)
                return f"ScrapeGraphAI[{provider.name}] extracted from {url}: {json.dumps(result, default=str)}"
            except Exception as exc:
                last_error = exc
                continue

        raise IntegrationError(f"ScrapeGraphAI failed for every configured provider: {last_error}")

    def send_notification(self, args: dict[str, Any], context: dict[str, Any]) -> str:
        message = str(args.get("message") or context.get("last_result") or "ResilientOS task completed.")
        if args.get("include_last_result") and context.get("last_result"):
            message = f"{message}\n\n{context['last_result']}"

        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            return self._send_telegram(message)
        if os.getenv("DISCORD_WEBHOOK_URL"):
            return self._send_discord(message)
        raise IntegrationNotConfigured(
            "No notification channel configured. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID or DISCORD_WEBHOOK_URL."
        )

    def _run_scrapegraph(self, url: str, prompt: str, provider: LLMProviderConfig) -> Any:
        _install_scrapegraphai_compat()
        try:
            from scrapegraphai.graphs import SmartScraperGraph
        except ImportError as exc:
            raise IntegrationNotConfigured(
                "Open-source ScrapeGraphAI or one of its LangChain dependencies is unavailable. "
                "Run `pip install -r requirements.txt` in a clean virtualenv."
            ) from exc

        graph_config = {
            "llm": provider.scrapegraph_llm_config(),
            "headless": os.getenv("SCRAPEGRAPH_HEADLESS", "true").lower() == "true",
            "verbose": os.getenv("SCRAPEGRAPH_VERBOSE", "false").lower() == "true",
        }
        graph = SmartScraperGraph(prompt=prompt, source=url, config=graph_config)
        return graph.run()

    def _send_telegram(self, message: str) -> str:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:3900]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return f"Telegram notification sent to chat {chat_id}."

    def _send_discord(self, message: str) -> str:
        response = requests.post(
            os.environ["DISCORD_WEBHOOK_URL"],
            json={"content": message[:1900]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return "Discord notification sent."


def _required(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not value:
        raise ValueError(f"Missing required argument: {key}")
    return str(value)


def _install_scrapegraphai_compat() -> None:
    """Bridge ScrapeGraphAI 2.1.1 to the newer LangChain package split."""
    try:
        import langchain_community.chat_models as community_chat_models
    except ImportError:
        return

    if hasattr(community_chat_models, "ChatOllama"):
        return

    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise IntegrationNotConfigured(
            "Open-source ScrapeGraphAI requires langchain-ollama. "
            "Run `pip install -r requirements.txt` in a clean virtualenv."
        ) from exc

    community_chat_models.ChatOllama = ChatOllama
