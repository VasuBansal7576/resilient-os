from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scrape_url",
            "description": "Extract structured information from a public URL using open-source ScrapeGraphAI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["url", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": "Send a real Telegram or Discord notification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "include_last_result": {"type": "boolean"},
                },
                "required": ["message"],
            },
        },
    },
]


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    args: dict[str, Any]


class AgentPlanner:
    """Builds a real tool plan via LLM when configured, with deterministic planning fallback."""

    def __init__(self, mode: str | None = None):
        self.mode = (mode or os.getenv("AGENT_MODE") or "auto").lower()
        self.last_backend = "scripted"
        self.last_error: str | None = None

    def plan(self, task: str) -> list[ToolInvocation]:
        if self.mode in {"auto", "llm"}:
            try:
                plan = self._plan_with_llm(task)
                if plan:
                    self.last_backend = self._backend_name()
                    self.last_error = None
                    return plan
            except Exception as exc:
                if self.mode == "llm":
                    raise
                self.last_error = str(exc)

        self.last_backend = "scripted"
        return self._scripted_plan(task)

    def summarize(self, task: str, tool_results: list[dict[str, str]]) -> str:
        successes = [result for result in tool_results if not result["result"].startswith("[")]
        failures = [result for result in tool_results if result["result"].startswith("[")]
        clean_task = task.strip().rstrip(".")
        summary = f"Completed {len(successes)} of {len(tool_results)} tool calls for: {clean_task}"
        if failures:
            failed_tools = ", ".join(result["tool"] for result in failures)
            return f"{summary}. Needs attention: {failed_tools}."
        return f"{summary}."

    def _plan_with_llm(self, task: str) -> list[ToolInvocation]:
        client, model = self._client_and_model()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Plan tool calls for the user task. Use only the provided tools. "
                        "For web extraction tasks, call scrape_url first. If the user asks to notify, "
                        "call send_notification after scraping and include the last result."
                    ),
                },
                {"role": "user", "content": task},
            ],
            tools=TOOLS,
            tool_choice="auto",
        )
        message = response.choices[0].message
        tool_calls = message.tool_calls or []
        plan: list[ToolInvocation] = []
        for tool_call in tool_calls:
            args = json.loads(tool_call.function.arguments or "{}")
            plan.append(ToolInvocation(tool_call.function.name, args))
        return plan

    def _client_and_model(self) -> tuple[OpenAI, str]:
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            return (
                OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key, timeout=8.0),
                os.getenv("GROQ_PLANNER_MODEL", "llama-3.3-70b-versatile"),
            )

        nvidia_key = os.getenv("NVIDIA_API_KEY")
        if nvidia_key:
            return (
                OpenAI(
                    base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                    api_key=nvidia_key,
                    timeout=8.0,
                ),
                os.getenv("NVIDIA_PLANNER_MODEL", "meta/llama-3.3-70b-instruct"),
            )

        raise RuntimeError("GROQ_API_KEY or NVIDIA_API_KEY is required for LLM planning")

    def _backend_name(self) -> str:
        if os.getenv("GROQ_API_KEY"):
            return "groq"
        if os.getenv("NVIDIA_API_KEY"):
            return "nvidia"
        return "none"

    def _scripted_plan(self, task: str) -> list[ToolInvocation]:
        url = _extract_url(task) or "https://example.com"
        lower = task.lower()
        plan: list[ToolInvocation] = []

        if any(word in lower for word in ("scrape", "pricing", "competitor", "price", "website")):
            plan.append(
                ToolInvocation(
                    "scrape_url",
                    {
                        "url": url,
                        "prompt": (
                            "Extract pricing, plan names, product names, notable details, and source links. "
                            "Return concise structured JSON."
                        ),
                    },
                )
            )

        if any(word in lower for word in ("notify", "notification", "telegram", "discord", "team", "message")):
            plan.append(
                ToolInvocation(
                    "send_notification",
                    {
                        "message": f"ResilientOS completed extraction for {url}.",
                        "include_last_result": True,
                    },
                )
            )

        if not plan:
            plan = [
                ToolInvocation(
                    "scrape_url",
                    {
                        "url": url,
                        "prompt": "Extract the most important useful facts from this page as structured JSON.",
                    },
                )
            ]
        return plan


def _extract_url(task: str) -> str | None:
    match = re.search(r"https?://[^\s,]+", task)
    if match:
        return match.group(0).rstrip(".")
    domain = re.search(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b", task)
    if domain:
        return f"https://{domain.group(0)}"
    return None
