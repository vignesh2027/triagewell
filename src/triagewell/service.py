"""Shared entry points for the CLI, the web dashboard and the MCP server."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .gateway import GatewayError, OtariClient
from .github import GitHubReader, load_issue_file, parse_ref
from .ledger import Ledger
from .outbox import write_local
from .triage import Triager

SAMPLES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "issues"


def list_samples() -> list[str]:
    if not SAMPLES.exists():
        return []
    return sorted(p.stem for p in SAMPLES.glob("*.json"))


def triage(
    settings: Settings,
    *,
    ref: str | None = None,
    sample: str | None = None,
    maintainer: str,
    budget: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Triage one issue, given as OWNER/REPO#N (or URL) or as a bundled sample name."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", maintainer or ""):
        raise ValueError("Maintainer must be a short name: letters, digits, '.', '_' or '-'.")
    if budget:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", budget):
            raise ValueError("Budget id must be letters, digits, '.', '_' or '-'.")
        settings = replace(settings, end_user_budget=budget)
    gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=settings.timeout_s)
    github = GitHubReader(settings.github_token)
    try:
        if sample:
            if sample not in list_samples():
                raise ValueError(f"Unknown sample {sample!r}. Choose one of: {', '.join(list_samples())}")
            issue = load_issue_file(SAMPLES / f"{sample}.json")
        elif ref:
            repo, number = parse_ref(ref)
            issue = github.issue(repo, number)
        else:
            raise ValueError("Give an issue reference or a sample name.")
        report = Triager(settings, gateway, github, on_event=on_event).run(issue, maintainer).to_dict()
        write_local(settings, report)
        return report
    finally:
        gateway.close()
        github.close()


def budget_status(settings: Settings, maintainer: str) -> dict[str, Any]:
    """What a maintainer has spent: Triagewell's own ledger beside Otari's end-user record."""
    allowance = Ledger(settings.home).allowance(maintainer, settings.daily_limit_usd, settings.issue_limit_usd)
    status: dict[str, Any] = {
        "maintainer": maintainer,
        "local": {
            "spent_today_usd": round(allowance.spent_today, 6),
            "daily_limit_usd": allowance.daily_limit,
            "remaining_usd": round(allowance.remaining, 6),
            "issue_limit_usd": allowance.issue_limit,
        },
        "otari": None,
        "otari_budget": None,
        "otari_error": None,
    }
    try:
        gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=15)
        try:
            status["otari"] = gateway.end_user(maintainer)
            if status["otari"]:
                budget_id = status["otari"].get("budget_id")
                status["otari_budget"] = next((b for b in gateway.end_user_budgets() if b.get("budget_id") == budget_id), None)
        finally:
            gateway.close()
    except (GatewayError, ValueError) as exc:
        status["otari_error"] = str(exc)[:200]
    except Exception as exc:  # network trouble must not break the dashboard
        status["otari_error"] = f"unreachable: {type(exc).__name__}"
    return status
