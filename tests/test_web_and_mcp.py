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
    monkeypatch.setattr(web, "budget_status", lambda s, name: {"maintainer": name, "local": {"spent_today_usd": 0.001, "daily_limit_usd": 0.5, "remaining_usd": 0.499, "issue_limit_usd": 0.05}, "otari": {"budget_id": "maintainer-daily", "spend": 0.001, "current_requests": 3}, "otari_budget": {"budget_id": "maintainer-daily", "max_budget": 0.25}, "otari_error": None})
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
    assert client.get("/assets/favicon.svg").status_code == 200
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


def test_dashboard_eval_page(tmp_path, monkeypatch):
    import json

    r = {"k": 3, "n": 4, "value": 0.75, "ci95": [0.3, 0.95]}
    none = {"k": 0, "n": 0, "value": None, "ci95": None}
    results = {
        "issues": 4, "completed": 4, "repos": {"o/r": 4},
        "kind": {"accuracy": r, "bug_label_precision": r, "bug_label_recall": none},
        "duplicates": {k: r for k in ("precision", "recall", "right_original", "search_found_original", "false_flags_on_non_duplicates")},
        "reproduction": {"outcomes": {"reproduced": 2}, "decisive": r, "reproduced_then_fixed": r, "fixed_and_reproduced": r, "refuted_then_not_fixed": none, "base_rate_fixed_among_ran": r},
        "grounding": {"share_unverified": r}, "safety": {"quarantined_real_issues": none},
        "cost_latency": {"total_usd_reported": 0.04, "per_issue_usd": {"median": 0.01}, "sandbox_runs": 3, "wall_s": {"median": 30, "p90": 50},
                         "otari_duration_ms_median": {"sorter": 400}, "calls": 12, "fallback_calls": 1, "mcp_calls_completed": 2},
        "per_issue": [{"ref": "o/r#1", "status": "triaged", "kind_true": "bug", "kind_pred": "bug", "dup_true": None, "dup_is_true": False,
                       "dup_pred": None, "repro": "reproduced", "pinned": "1.2.3", "fixed": True, "cost_usd": 0.0103, "wall_s": 31.0}],
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(results))
    monkeypatch.setattr(web, "EVAL_RESULTS", path)
    client = TestClient(web.create_app(settings=make_settings(tmp_path)))
    text = client.get("/eval").text
    assert "3/4</b> = 75%" in text and "o/r#1" in text and "1.2.3" in text and "n/a" in text
    monkeypatch.setattr(web, "EVAL_RESULTS", tmp_path / "missing.json")
    assert "No results yet" in client.get("/eval").text
