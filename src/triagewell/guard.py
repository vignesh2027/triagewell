"""Local safety checks that sit around the gateway's own guardrails.

Otari's inference guardrail (the `prompt-injection` profile) judges whether issue
text is trying to steer the model. These checks cover what that profile does
not: credentials pasted into an issue, which must never be forwarded to a model
provider or echoed into a public reply, and a cheap first-pass injection signal
that still works when the gateway's guardrail service is unavailable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("otari_key", re.compile(r"\b(?:otk|tk)[_-][A-Za-z0-9_-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "assigned_secret",
        re.compile(r"(?im)^(\s*[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY)[A-Z0-9_]*\s*[=:]\s*)(['\"]?)(?!\[REDACTED)([^\s'\"]{8,})\2"),
    ),
]

INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)",
        r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|system) (?:instructions|prompt)",
        r"you are now (?:a|an|in) ",
        r"(?:reveal|print|show|repeat) (?:your|the) (?:system prompt|hidden instructions|instructions)",
        r"(?:label|close|mark) this (?:issue )?as (?:critical|security|resolved|wontfix)",
        r"(?:send|post|upload|exfiltrate) (?:the |all |your )?(?:secrets?|tokens?|keys?|env(?:ironment)?)",
        r"(?:fetch|visit|open|curl) https?://\S+\?(?:\S*=)\{",
        r"<\s*/?\s*(?:system|assistant)\s*>",
        r"\bBEGIN (?:SYSTEM|ADMIN) (?:PROMPT|OVERRIDE)\b",
        r"as an ai (?:language )?model,? you must",
    ]
]


@dataclass
class Redaction:
    text: str
    found: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.found)


def redact_secrets(text: str) -> Redaction:
    found: list[str] = []

    def mask(kind: str):
        def _replace(match: re.Match[str]) -> str:
            found.append(kind)
            if kind == "assigned_secret":
                return f"{match.group(1)}{match.group(2)}[REDACTED:{kind}]{match.group(2)}"
            return f"[REDACTED:{kind}]"

        return _replace

    for kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(mask(kind), text)
    return Redaction(text=text, found=found)


def injection_signals(text: str) -> list[str]:
    hits = []
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(match.group(0)[:80])
    return hits


def leaks_in_output(text: str) -> list[str]:
    """Secrets that would leave in a drafted reply. Anything here blocks posting."""
    return [kind for kind, pattern in SECRET_PATTERNS if pattern.search(text)]


def fence_untrusted(label: str, text: str) -> str:
    """Wrap untrusted text so the model sees clear boundaries around it."""
    safe = text.replace("<<<", "< < <").replace(">>>", "> > >")
    return f"<<<UNTRUSTED {label} START>>>\n{safe}\n<<<UNTRUSTED {label} END>>>"
