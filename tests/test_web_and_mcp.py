import asyncio

from conftest import FIXTURES, make_settings
from starlette.testclient import TestClient

from triagewell import mcp_server, web
from triagewell.github import load_issue_file
from triagewell.render import features_used
from triagewell.triage import Triager


def _report(tmp_path, clients, fixture="pasted-token.json"):
    settings = make_settings(tmp_path)
    gw, gh = clients()
    report = Triager(settings, gw, gh).run(load_issue_file(FIXTURES / fixture), "alice").to_dict()
    return settings, report


def test_features_used_reflects_the_run(tmp_path, clients):
    _, report = _report(tmp_path, clients)
    used = features_used(report)
    assert set(used) == {"routing", "roles", "fetch", "code", "mcp", "guardrails", "user_budget", "limits"}
    assert "alice" in used["user_budget"]


def test_quarantined_run_shows_no_tools(tmp_path, clients):
    _, report = _report(tmp_path, clients, "prompt-injection.json")
    used = features_used(report)
    assert "fetch" not in used and "mcp" not in used and "code" not in used


def test_dashboard_pages(tmp_path, clients, monkeypatch):
    settings, report = _report(tmp_path, clients)
    monkeypatch.setattr(web, "budget_status", lambda s, name: {"maintainer": name, "local": {"spent_today_usd": 0.001, "daily_limit_usd": 0.5, "remaining_usd": 0.499, "issue_limit_usd": 0.05}, "otari": {"budget_id": "maintainer-daily", "spend": 0.001, "current_requests": 3}, "otari_error": None})
    client = TestClient(web.create_app(settings=settings))
    home = client.get("/")
    assert home.status_code == 200 and "Recent triage" in home.text and report["id"] in home.text
    page = client.get(f"/report/{report['id']}")
    assert page.status_code == 200
    assert "Otari features this run used" in page.text and "Draft reply (not posted)" in page.text
    assert "ghp_FAKE" not in page.text
    assert client.get(f"/report/{report['id']}.json").json()["id"] == report["id"]
    assert "maintainer-daily" in client.get("/budgets").text
    assert client.get("/assets/logo.svg").status_code == 200
    assert client.get("/assets/..%2Fpyproject.toml").status_code == 404
    assert "No such report" in client.get("/report/nothing").text


def test_dashboard_post_runs_triage_and_redirects(tmp_path, clients, monkeypatch):
    settings, report = _report(tmp_path, clients)
    seen = {}

    def fake_triage(s, *, ref=None, sample=None, maintainer, budget=None):
        seen.update(ref=ref, sample=sample, maintainer=maintainer)
        return report

    monkeypatch.setattr(web, "triage", fake_triage)
    client = TestClient(web.create_app(settings=settings))
    response = client.post("/triage", data={"ref": "", "sample": "pasted-token", "maintainer": "bob"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/report/{report['id']}"
    assert seen == {"ref": None, "sample": "pasted-token", "maintainer": "bob"}
    refused = client.post("/triage", data={"maintainer": "bob"}, headers={"origin": "https://evil.example"})
    assert refused.status_code == 403


def test_dashboard_shows_errors(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)

    def boom(*a, **k):
        raise ValueError("Give an issue reference or a sample name.")

    monkeypatch.setattr(web, "triage", boom)
    client = TestClient(web.create_app(settings=settings))
    response = client.post("/triage", data={"maintainer": "bob"})
    assert response.status_code == 200 and "Give an issue reference" in response.text


def test_mcp_server_tools(tmp_path, clients, monkeypatch):
    settings, report = _report(tmp_path, clients)
    monkeypatch.setattr(mcp_server, "load_settings", lambda env=None: settings)
    server = mcp_server.build()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"triage_issue", "triage_sample", "list_samples", "recent_reports", "get_report", "maintainer_budget"}
    result = asyncio.run(server.call_tool("recent_reports", {"limit": 5}))
    assert report["id"] in str(result)
    result = asyncio.run(server.call_tool("get_report", {"report_id": report["id"]}))
    assert "Draft reply" in str(result)
