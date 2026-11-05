import json

from conftest import FIXTURES, make_settings

from triagewell.github import load_issue_file
from triagewell.ledger import Ledger
from triagewell.triage import Triager, load_report


def run(tmp_path, clients, fixture="pasted-token.json", maintainer="alice", **overrides):
    settings = make_settings(tmp_path, **overrides)
    gw, gh = clients()
    issue = load_issue_file(FIXTURES / fixture)
    return settings, Triager(settings, gw, gh).run(issue, maintainer)


def test_full_triage_uses_every_gateway_feature(tmp_path, clients, fake_gateway):
    settings, report = run(tmp_path, clients)
    assert report.status == "triaged"

    chats = fake_gateway.bodies("/chat/completions")
    sorter, investigator, screen = chats
    # Two open-weight roles plus a safety screen, each asked for by its own name.
    assert sorter["model"] == "triagewell-sorter"
    assert investigator["model"] == "triagewell-investigator"
    assert screen["model"] == "meta/llama-guard-4-12b"
    # Per-user budgets: every call is billed to the maintainer as an Otari end user.
    assert {c["user"] for c in chats} == {"alice"}
    # Guardrails: the sorter call carries Otari's prompt-injection check.
    assert sorter["guardrails"] == [{"profile": "prompt-injection", "mode": "monitor"}]
    # Web fetch, code execution and MCP on the investigator call.
    types = {t["type"] for t in investigator["tools"]}
    assert types == {"otari_web_fetch", "otari_code_execution"}
    assert investigator["mcp_servers"][0]["url"] == "https://mcp.deepwiki.com/mcp"
    assert investigator["mcp_servers"][0]["allowed_tools"] == ["read_wiki_structure", "ask_wiki_question"]
    assert investigator["max_tool_iterations"] == 6
    # Spending limits: every call sends a realistic output cap so Otari's hold stays small.
    assert sorter["max_tokens"] == 600 and investigator["max_tokens"] == 1800
    # The reporter's snippet ran in Otari's sandbox with no model involved.
    code = fake_gateway.bodies("/code/execute")
    assert len(code) == 1 and "json.dumps(prices)" in code[0]["code"] and code[0]["user"] == "alice"
    assert report.repro["reproduced"] is True


def test_secret_never_reaches_the_gateway(tmp_path, clients, fake_gateway):
    _, report = run(tmp_path, clients)
    sent = json.dumps([r["body"] for r in fake_gateway.requests])
    assert "ghp_FAKE" not in sent
    assert "github_token" in report.guard["secrets_redacted"]


def test_report_records_routing_and_cost(tmp_path, clients, fake_gateway):
    fake_gateway.fallback_roles = {"investigator"}
    settings, report = run(tmp_path, clients)
    by_role = {c["role"]: c for c in report.calls}
    assert by_role["investigator"]["fallback"] is True
    assert by_role["investigator"]["attempts"] == 2
    assert by_role["sorter"]["provider"] == "nebius"
    assert report.cost_usd == round(0.00005 + 0.0009 + 0.00001, 6)
    assert Ledger(settings.home).spent_today("alice") == report.cost_usd
    assert load_report(settings.home, report.id)["status"] == "triaged"


def test_labels_and_duplicates_are_checked(tmp_path, clients):
    _, report = run(tmp_path, clients)
    assert report.investigation["labels"] == ["question", "enhancement"]
    assert report.investigation["duplicate_of"] == 101
    assert [d["number"] for d in report.duplicates] == [101]


def test_duplicate_not_found_by_search_is_dropped(tmp_path, clients, fake_gateway):
    fake_gateway.investigation_reply = {**fake_gateway.investigation_reply, "duplicate_of": 4242}
    _, report = run(tmp_path, clients)
    assert report.investigation["duplicate_of"] is None
    assert any("4242" in n for n in report.notes)


def test_prompt_injection_is_quarantined(tmp_path, clients, fake_gateway):
    _, report = run(tmp_path, clients, fixture="prompt-injection.json")
    assert report.status == "quarantined"
    assert report.guard["injection_local"]
    investigator = fake_gateway.bodies("/chat/completions")[1]
    assert "tools" not in investigator and "mcp_servers" not in investigator
    assert fake_gateway.bodies("/code/execute") == []
    assert report.investigation["reply_draft"] == ""
    assert "security-review" in report.investigation["labels"]
    assert "critical" not in json.dumps(report.investigation["labels"])
    assert report.investigation["next_step"].startswith("Read this issue yourself")


def test_gateway_guardrail_verdict_alone_quarantines(tmp_path, clients, fake_gateway):
    fake_gateway.guardrail_verdict = {"profile": "prompt-injection", "mode": "monitor", "valid": False, "score": 0.98, "status": "evaluated"}
    _, report = run(tmp_path, clients)
    assert report.status == "quarantined"
    assert report.guard["injection_local"] == []
    assert report.guard["injection_gateway"][0]["valid"] is False


def test_budget_refusal_defers_the_issue(tmp_path, clients, fake_gateway):
    fake_gateway.budget_refusal_on = "investigator"
    settings, report = run(tmp_path, clients)
    assert report.status == "deferred_budget"
    assert report.budget["otari_refusal"]["scope"] == "user"
    assert report.budget["otari_refusal"]["retry_after_s"] == 3600
    assert report.cost_usd == 0.00005


def test_local_daily_limit_stops_before_the_gateway(tmp_path, clients, fake_gateway):
    settings = make_settings(tmp_path, daily_limit_usd=0.10, issue_limit_usd=0.05)
    Ledger(settings.home).record("bob", "x/y#1", 0.08, "triaged")
    gw, gh = clients()
    report = Triager(settings, gw, gh).run(load_issue_file(FIXTURES / "pasted-token.json"), "bob")
    assert report.status == "deferred_budget"
    assert fake_gateway.requests == []


def test_missing_policy_falls_back_to_catalog_model(tmp_path, clients, fake_gateway):
    fake_gateway.unknown_models = {"triagewell-sorter"}
    _, report = run(tmp_path, clients)
    models = [b["model"] for b in fake_gateway.bodies("/chat/completions")]
    assert models[:2] == ["triagewell-sorter", "nvidia/nemotron-3.5-lightning-30b-a3b"]
    assert any("not defined" in n for n in report.notes)
    assert report.status == "triaged"


def test_reply_with_secret_is_redacted_and_flagged(tmp_path, clients, fake_gateway):
    fake_gateway.investigation_reply = {**fake_gateway.investigation_reply, "reply_draft": "Use this key: sk-proj-abcdefghijklmnopqrstuvwxyz123"}
    _, report = run(tmp_path, clients)
    assert "sk-proj" not in report.investigation["reply_draft"]
    assert report.guard["reply_leaks"] == ["openai_style_key"]


def test_non_json_investigator_reply_is_kept_as_text(tmp_path, clients, fake_gateway):
    fake_gateway.investigation_reply = "I could not decide."
    _, report = run(tmp_path, clients)
    assert report.investigation["summary"] == "I could not decide."
    assert report.investigation["confidence"] == "low"


def test_screen_unsafe_verdict_is_recorded(tmp_path, clients, fake_gateway):
    fake_gateway.screen_reply = "unsafe\nS7"
    _, report = run(tmp_path, clients)
    assert report.guard["screen"]["verdict"] == "unsafe"


def test_stored_mcp_server_id_replaces_inline_server(tmp_path, clients, fake_gateway):
    run(tmp_path, clients, mcp_server_id="11111111-1111-1111-1111-111111111111")
    investigator = fake_gateway.bodies("/chat/completions")[1]
    assert investigator["mcp_server_ids"] == ["11111111-1111-1111-1111-111111111111"]
    assert "mcp_servers" not in investigator


def test_github_is_only_read(tmp_path, clients, fake_github):
    run(tmp_path, clients)
    assert fake_github.requests and all(r.method == "GET" for r in fake_github.requests)


def test_hold_refusal_retries_with_smaller_output_cap(tmp_path, clients, fake_gateway):
    fake_gateway.hold_limit_tokens = 600
    _, report = run(tmp_path, clients)
    caps = [b["max_tokens"] for b in fake_gateway.bodies("/chat/completions") if b["metadata"]["role"] == "investigator"]
    assert caps == [1800, 480]
    assert report.status == "triaged"
    assert any("retried with max_tokens 480" in n for n in report.notes)


def test_hold_refusal_too_small_to_retry_defers(tmp_path, clients, fake_gateway):
    fake_gateway.hold_limit_tokens = 100
    _, report = run(tmp_path, clients, investigator=make_settings(tmp_path).investigator.__class__("investigator", "triagewell-investigator", None, 900))
    assert report.status == "deferred_budget"
