from __future__ import annotations

import json
from typing import Any


class IntegrationTestDouble:
    """Deterministic test double for external integrations."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def call(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> str:
        context = context or {}
        self.calls.append((name, args, dict(context)))

        if name == "scrape_url":
            url = args["url"]
            source = "cached ScrapeGraphAI result" if context.get("use_backup") or context.get("use_cache") else url
            result = {"url": url, "price": "$99", "competitor": "Acme", "source": source}
            return f"ScrapeGraphAI[test-double] extracted from {source}: {json.dumps(result, sort_keys=True)}"

        if name == "send_notification":
            message = args.get("message", "ResilientOS task completed.")
            if args.get("include_last_result") and context.get("last_result"):
                message = f"{message}\n\n{context['last_result']}"
            return f"Notification[test-double] sent: {message}"

        raise ValueError(f"Unknown test integration tool: {name}")
