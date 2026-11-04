"""Test doubles: a fake Otari gateway and a fake GitHub API, both on
httpx.MockTransport, so the suite runs offline and without credentials."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from triagewell.config import Role, Settings
from triagewell.gateway import OtariClient
from triagewell.github import GitHubReader

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "issues"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = Settings(
        otari_base_url="https://gateway.test/api/v1",
        otari_api_key="tk-test",
        sorter=Role("sorter", "triagewell-sorter", "nvidia/nemotron-3.5-lightning-30b-a3b", 600),
        investigator=Role("investigator", "triagewell-investigator", "openai/gpt-oss-120b", 1800),
        screen=Role("screen", "meta/llama-guard-4-12b", None, 20),
        injection_profile="prompt-injection",
        injection_mode="monitor",
        mcp_url="https://mcp.deepwiki.com/mcp",
        mcp_server_id=None,
        mcp_allowed_tools=["read_wiki_structure", "ask_wiki_question"],
        end_user_budget=None,
        daily_limit_usd=0.50,
        issue_limit_usd=0.05,
        github_token=None,
        allow_post=False,
        post_repos=[],
        home=tmp_path / "home",
        code_execution=True,
        web_fetch=True,
        max_tool_iterations=6,
    )
    return replace(base, **overrides)


SORT_REPLY = {
    "kind": "bug",
    "area": "json encoder",
    "severity": "medium",
    "summary": "json.dumps raises on Decimal values.",
    "search_terms": ["json", "Decimal", "dumps"],
    "links": ["https://docs.python.org/3/library/json.html"],
    "needs_code_run": True,
}

INVESTIGATION_REPLY = {
    "summary": "json.dumps does not know Decimal. The sandbox shows the TypeError.",
    "likely_cause": "The default JSONEncoder has no Decimal support.",
    "evidence": [
        "sandbox: TypeError: Object of type Decimal is not JSON serializable",
        "fetched https://docs.python.org/3/library/json.html: the conversion table has no Decimal",
        "wiki: the encoder dispatches on a fixed set of types",
    ],
    "duplicate_of": 101,
    "labels": ["question", "Enhancement", "made-up-label"],
    "next_step": "Point the reporter at the default= hook.",
    "reply_draft": "Thanks for the report. json.dumps has no built-in Decimal support; pass default=float.",
    "confidence": "high",
}


class FakeGateway:
    """Records every request and answers like Otari would."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.sort_reply: dict[str, Any] | str = SORT_REPLY
        self.investigation_reply: dict[str, Any] | str = INVESTIGATION_REPLY
        self.screen_reply = "safe"
        self.guardrail_verdict: dict[str, Any] = {"profile": "prompt-injection", "mode": "monitor", "valid": True, "score": 0.01, "status": "evaluated"}
        self.unknown_models: set[str] = set()
        self.budget_refusal_on: str | None = None
        self.code_result = {"stdout": "", "stderr": "Traceback (most recent call last):\nTypeError: Object of type Decimal is not JSON serializable\n", "return_code": 1}
        self.tool_calls = [
            {"id": "otari_mcp_1", "type": "mcp", "status": "completed", "server": "repo_wiki", "name": "ask_wiki_question", "output": "The encoder handles dict, list, str, int, float, bool, None."},
            {"id": "otari_wf_1", "type": "web_fetch", "status": "completed", "url": "https://docs.python.org/3/library/json.html"},
        ]
        self.fallback_roles: set[str] = set()

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [r["body"] for r in self.requests if r["path"].endswith(path)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({"path": request.url.path, "body": body, "headers": dict(request.headers)})
        path = request.url.path
        if path.endswith("/chat/completions"):
            return self._chat(body)
        if path.endswith("/code/execute"):
            return httpx.Response(200, json={"object": "code_execution.result", **self.code_result, "files": []}, headers={"Otari-Request-ID": "req-code"})
        if path.endswith("/tools"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "otari_web_fetch", "available": True}]})
        if path.endswith("/end-users"):
            return httpx.Response(200, json={"data": [{"external_id": request.url.params.get("external_id"), "budget_id": "maintainer-daily", "spend": 0.01}]})
        return httpx.Response(404, json={"detail": "Not Found"})

    def _chat(self, body: dict[str, Any]) -> httpx.Response:
        model = body["model"]
        role = (body.get("metadata") or {}).get("role")
        if model in self.unknown_models:
            return httpx.Response(400, json={"detail": f"Unknown model {model}", "code": "invalid_model"}, headers={"Otari-Error-Code": "invalid_model"})
        if self.budget_refusal_on == role:
            return httpx.Response(
                403,
                json={"detail": "User 'alice' has exceeded budget limit", "code": "budget_exceeded"},
                headers={"Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "user", "Retry-After": "3600"},
            )
        headers = {
            "Otari-Request-ID": f"req-{role}",
            "Otari-Provider": "deepinfra" if role != "sorter" else "nebius",
            "Otari-Model": {"sorter": "nvidia/Nemotron-3_5-Lightning", "investigator": "openai/gpt-oss-120b", "screen": "meta-llama/Llama-Guard-4-12B"}.get(role, model),
            "Otari-Attempt-Count": "2" if role in self.fallback_roles else "1",
            "Otari-Fallback": "true" if role in self.fallback_roles else "false",
        }
        tool_calls: list[dict[str, Any]] = []
        if role == "sorter":
            content = self.sort_reply if isinstance(self.sort_reply, str) else json.dumps(self.sort_reply)
            if body.get("guardrails"):
                headers["Otari-Guardrails"] = json.dumps([self.guardrail_verdict])
            cost = "0.000050"
        elif role == "investigator":
            reply = self.investigation_reply
            content = reply if isinstance(reply, str) else "Here is what I found.\n```json\n" + json.dumps(reply) + "\n```"
            if body.get("tools") or body.get("mcp_servers"):
                tool_calls = self.tool_calls
            cost = "0.000900"
        else:
            content = self.screen_reply
            cost = "0.000010"
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost_usd": cost, "pricing_source": "defaults"},
        }
        if tool_calls:
            payload["otari_tool_calls"] = tool_calls
        return httpx.Response(200, json=payload, headers=headers)


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.search_items = [
            {"number": 101, "title": "json cannot encode Decimal", "state": "closed", "html_url": "https://github.com/python/cpython/issues/101"},
            {"number": 0, "title": "self", "state": "open", "html_url": ""},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "GET":
            return httpx.Response(405, json={"message": "read-only fake"})
        if request.url.path == "/search/issues":
            return httpx.Response(200, json={"items": self.search_items})
        if "/issues/" in request.url.path:
            number = int(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"number": number, "title": "Example", "body": "body", "user": {"login": "someone"}, "labels": [], "html_url": f"https://github.com/o/r/issues/{number}", "state": "open"})
        return httpx.Response(404, json={})


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def clients(fake_gateway: FakeGateway, fake_github: FakeGitHub) -> Callable[[], tuple[OtariClient, GitHubReader]]:
    def build() -> tuple[OtariClient, GitHubReader]:
        gw = OtariClient("https://gateway.test/api/v1", "tk-test", transport=httpx.MockTransport(fake_gateway.handler))
        gh = GitHubReader(transport=httpx.MockTransport(fake_github.handler))
        return gw, gh

    return build
