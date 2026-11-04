import json

import httpx
import pytest
from conftest import make_settings

from triagewell.gateway import BudgetExceeded, GuardrailBlocked, OtariClient, UnknownModel
from triagewell.outbox import PostRefused, check_postable, post_github, write_local


def client_for(handler):
    return OtariClient("https://gateway.test/api/v1", "tk-test", transport=httpx.MockTransport(handler))


def test_chat_reads_otari_headers_and_tool_calls():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer tk-test"
        assert request.headers["Otari-End-User-Budget"] == "maintainer-daily"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"cost_usd": "0.000123"},
                "otari_tool_calls": [{"id": "otari_ci_1", "type": "code_execution", "status": "completed"}],
            },
            headers={
                "Otari-Provider": "together",
                "Otari-Model": "openai/gpt-oss-120b",
                "Otari-Fallback": "true",
                "Otari-Attempt-Count": "2",
                "Otari-Request-ID": "abc",
                "Otari-Guardrails": json.dumps([{"profile": "prompt-injection", "valid": False}]),
            },
        )

    result = client_for(handler).chat("triagewell-investigator", [{"role": "user", "content": "x"}], end_user_budget="maintainer-daily")
    assert result.content == "hi"
    assert result.provider == "together" and result.fallback and result.attempt_count == 2
    assert result.cost_usd == pytest.approx(0.000123)
    assert result.guardrail_flagged
    assert result.tool_calls[0]["type"] == "code_execution"


@pytest.mark.parametrize(
    "code,exc",
    [("budget_exceeded", BudgetExceeded), ("guardrail_violation", GuardrailBlocked), ("invalid_model", UnknownModel)],
)
def test_error_codes_map_to_exceptions(code, exc):
    def handler(request):
        return httpx.Response(403, json={"detail": "no", "code": code}, headers={"Otari-Error-Code": code, "Otari-Budget-Scope": "api_token", "Retry-After": "60"})

    with pytest.raises(exc) as info:
        client_for(handler).chat("m", [{"role": "user", "content": "x"}])
    if exc is BudgetExceeded:
        assert info.value.scope == "api_token" and info.value.retry_after == 60


def test_missing_key_is_a_clear_error():
    with pytest.raises(ValueError, match="OTARI_API_KEY"):
        OtariClient("https://gateway.test/api/v1", None)


def _report(status="triaged", reply="Thanks, looking into it.", **guard):
    return {
        "id": "abcdef123456",
        "status": status,
        "issue": {"repo": "me/sandbox-repo", "number": 3},
        "investigation": {"reply_draft": reply},
        "guard": guard,
    }


def test_posting_is_off_by_default(tmp_path):
    with pytest.raises(PostRefused, match="off"):
        check_postable(make_settings(tmp_path), _report())


def test_posting_needs_every_switch(tmp_path):
    settings = make_settings(tmp_path, allow_post=True, post_repos=["me/sandbox-repo"], github_token="t")
    assert "Thanks" in check_postable(settings, _report())
    with pytest.raises(PostRefused):
        check_postable(make_settings(tmp_path, allow_post=True, post_repos=["other/repo"], github_token="t"), _report())
    with pytest.raises(PostRefused):
        check_postable(settings, _report(status="quarantined"))
    with pytest.raises(PostRefused):
        check_postable(settings, _report(screen={"verdict": "unsafe"}))
    with pytest.raises(PostRefused):
        check_postable(settings, _report(reply="token ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE1234"))


def test_post_github_sends_one_comment(tmp_path):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"html_url": "https://github.com/me/sandbox-repo/issues/3#c1"})

    settings = make_settings(tmp_path, allow_post=True, post_repos=["me/sandbox-repo"], github_token="t")
    url = post_github(settings, _report(), transport=httpx.MockTransport(handler))
    assert url.endswith("#c1")
    assert seen[0][0] == "POST" and seen[0][1] == "/repos/me/sandbox-repo/issues/3/comments"


def test_local_outbox(tmp_path):
    settings = make_settings(tmp_path)
    path = write_local(settings, _report())
    assert path.exists() and "Thanks" in path.read_text()
    assert write_local(settings, _report(reply="")) is None
