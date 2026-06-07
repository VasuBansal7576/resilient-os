from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse


class SecurityPolicyViolation(ValueError):
    """Raised when a tool request leaves the authorized public-web scope."""


BLOCKED_HOSTNAMES = {
    "0.0.0.0",
    "localhost",
    "metadata",
    "metadata.google.internal",
}
BLOCKED_SUFFIXES = (
    ".internal",
    ".intranet",
    ".lan",
    ".local",
    ".localhost",
)
ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class PromptSecurityFinding:
    category: str
    severity: str
    evidence: str


PROMPT_ABUSE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "direct_role_override",
        "critical",
        r"\b(ignore|disregard|override)\b.{0,40}\b(previous|prior|above|system|developer)\b.{0,40}\b(instructions?|rules?|prompt)\b",
    ),
    (
        "system_prompt_extraction",
        "high",
        r"\b(reveal|repeat|print|show|dump)\b.{0,40}\b(system prompt|developer message|hidden instructions?)\b",
    ),
    (
        "tool_abuse",
        "critical",
        r"\b(call|run|invoke|use)\b.{0,40}\b(delete_files?|rm -rf|exfiltrate|upload secrets?|bypass approval)\b",
    ),
    (
        "credential_exfiltration",
        "critical",
        r"\b(send|post|upload|exfiltrate|leak)\b.{0,40}\b(api keys?|tokens?|credentials?|\.env|secrets?)\b",
    ),
)


def validate_public_http_url(raw_url: str) -> str:
    """Allow only public HTTP(S) targets for scraper tools.

    This keeps demo and red-team runs inside a safe, authorized public-web
    boundary. It blocks common SSRF targets such as localhost, private IPs,
    link-local metadata services, and non-web schemes.
    """
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SecurityPolicyViolation(f"Security policy blocked non-HTTP URL scheme: {parsed.scheme or 'missing'}")
    if parsed.username or parsed.password:
        raise SecurityPolicyViolation("Security policy blocked URL with embedded credentials.")
    if not parsed.hostname:
        raise SecurityPolicyViolation("Security policy blocked URL without a hostname.")

    host = parsed.hostname.strip().strip("[]").lower().rstrip(".")
    if host in BLOCKED_HOSTNAMES or any(host.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        raise SecurityPolicyViolation(f"Security policy blocked internal hostname: {host}")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return raw_url

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise SecurityPolicyViolation(f"Security policy blocked non-public IP address: {host}")
    return raw_url


def assess_prompt_security(text: str) -> list[PromptSecurityFinding]:
    findings: list[PromptSecurityFinding] = []
    for category, severity, pattern in PROMPT_ABUSE_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            evidence = " ".join(match.group(0).split())
            findings.append(PromptSecurityFinding(category=category, severity=severity, evidence=evidence[:160]))
    return findings
