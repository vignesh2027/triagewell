"""Read-only access to public GitHub issues.

Nothing in this module writes to GitHub. Posting a comment lives in outbox.py
behind two explicit switches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

API = "https://api.github.com"

_REF = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)$")
_URL = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<number>\d+)")
_STOP = set(
    "a an the and or but if then else when of to in on for with without from by is are was were be been "
    "it its this that these those i we you they he she my our your their not no can cannot can't does "
    "doesn't do don't using use used get got after before into out up down how what why which there here "
    "error bug issue problem works working fails failed failing please help".split()
)


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    body: str
    author: str
    labels: list[str] = field(default_factory=list)
    url: str = ""
    created_at: str = ""
    state: str = "open"
    comments: int = 0

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Candidate:
    number: int
    title: str
    state: str
    url: str


def parse_ref(text: str) -> tuple[str, int]:
    text = text.strip()
    for pattern in (_REF, _URL):
        match = pattern.match(text)
        if match:
            return f"{match['owner']}/{match['repo']}", int(match["number"])
    raise ValueError(f"Expected OWNER/REPO#NUMBER or an issue URL, got {text!r}")


def load_issue_file(path: str | Path) -> Issue:
    """Load an issue from a local JSON file (used for fixtures and offline demos)."""
    data = json.loads(Path(path).read_text())
    return Issue(
        repo=data["repo"],
        number=int(data["number"]),
        title=data["title"],
        body=data.get("body") or "",
        author=data.get("author", "unknown"),
        labels=list(data.get("labels") or []),
        url=data.get("url", ""),
        created_at=data.get("created_at", ""),
    )


def keywords(text: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_.]{2,}", text)
    seen: list[str] = []
    for word in words:
        low = word.lower().strip(".")
        if low in _STOP or low in seen or len(low) < 3:
            continue
        seen.append(low)
        if len(seen) >= limit:
            break
    return seen


class GitHubReader:
    def __init__(self, token: str | None = None, *, transport: httpx.BaseTransport | None = None):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "triagewell/0.1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=API, headers=headers, timeout=30, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: Any) -> Any:
        response = self._http.get(path, params=params or None)
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("GitHub rate limit reached. Set GITHUB_TOKEN (read-only scope is enough) or wait.")
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _issue(repo: str, data: dict[str, Any]) -> Issue:
        return Issue(
            repo=repo,
            number=int(data["number"]),
            title=data.get("title") or "",
            body=data.get("body") or "",
            author=(data.get("user") or {}).get("login", "unknown"),
            labels=[label["name"] for label in data.get("labels") or [] if isinstance(label, dict)],
            url=data.get("html_url", ""),
            created_at=data.get("created_at", ""),
            state=data.get("state", "open"),
            comments=int(data.get("comments") or 0),
        )

    def issue(self, repo: str, number: int) -> Issue:
        return self._issue(repo, self._get(f"/repos/{repo}/issues/{number}"))

    def recent_issues(self, repo: str, *, since_hours: int = 48, limit: int = 5) -> list[Issue]:
        since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = self._get(f"/repos/{repo}/issues", state="open", since=since, sort="created", direction="desc", per_page=30)
        issues = [self._issue(repo, item) for item in items if "pull_request" not in item and item.get("created_at", "") >= since]
        return issues[:limit]

    def similar(self, issue: Issue, terms: list[str], limit: int = 5) -> list[Candidate]:
        """Search the same repo for issues sharing the given terms (duplicates check)."""
        terms = [t for t in terms if t][:5] or keywords(issue.title)
        if not terms:
            return []
        query = f"repo:{issue.repo} is:issue " + " ".join(terms)
        try:
            data = self._get("/search/issues", q=query, per_page=limit + 1)
        except (httpx.HTTPError, RuntimeError):
            return []
        found = []
        for item in data.get("items", []):
            if int(item["number"]) == issue.number:
                continue
            found.append(Candidate(int(item["number"]), item.get("title", ""), item.get("state", ""), item.get("html_url", "")))
        return found[:limit]
