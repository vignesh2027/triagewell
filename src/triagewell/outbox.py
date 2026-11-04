"""Where a drafted reply goes. Safe by default.

By default a reply is written to a local outbox folder and nothing leaves the
machine. Posting to GitHub needs all of: TRIAGEWELL_ALLOW_POST=1, the repo on
TRIAGEWELL_POST_REPOS, a GITHUB_TOKEN, the `--post` flag on the command, a
triaged (not quarantined) report, and a reply that passed the leak and safety
checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .guard import leaks_in_output
from .render import draft_comment


class PostRefused(Exception):
    pass


def write_local(settings: Settings, report: dict[str, Any]) -> Path | None:
    body = draft_comment(report)
    if not body:
        return None
    issue = report["issue"]
    path = settings.outbox / f"{issue['repo'].replace('/', '__')}__{issue['number']}__{report['id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def check_postable(settings: Settings, report: dict[str, Any]) -> str:
    repo = report["issue"]["repo"]
    if not settings.allow_post:
        raise PostRefused("Posting is off. Set TRIAGEWELL_ALLOW_POST=1 to allow it.")
    if repo not in settings.post_repos:
        raise PostRefused(f"{repo} is not on TRIAGEWELL_POST_REPOS.")
    if not settings.github_token:
        raise PostRefused("GITHUB_TOKEN is not set.")
    if report.get("status") != "triaged":
        raise PostRefused(f"Report status is {report.get('status')}; only triaged reports can be posted.")
    guard = report.get("guard") or {}
    if guard.get("reply_leaks"):
        raise PostRefused("The reply had secret-shaped text in it.")
    if (guard.get("screen") or {}).get("verdict") == "unsafe":
        raise PostRefused("The safety screen marked the reply unsafe.")
    body = draft_comment(report)
    if not body:
        raise PostRefused("There is no reply to post.")
    if leaks_in_output(body):
        raise PostRefused("The reply had secret-shaped text in it.")
    return body


def post_github(settings: Settings, report: dict[str, Any], *, transport: httpx.BaseTransport | None = None) -> str:
    body = check_postable(settings, report)
    issue = report["issue"]
    with httpx.Client(
        base_url="https://api.github.com",
        headers={"Authorization": f"Bearer {settings.github_token}", "Accept": "application/vnd.github+json"},
        timeout=30,
        transport=transport,
    ) as http:
        response = http.post(f"/repos/{issue['repo']}/issues/{issue['number']}/comments", json={"body": body})
        response.raise_for_status()
        return response.json().get("html_url", "")
