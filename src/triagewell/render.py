"""Turn a report into Markdown: the maintainer summary and the draft comment."""

from __future__ import annotations

from typing import Any

STATUS_TEXT = {
    "triaged": "Triaged",
    "quarantined": "Quarantined: possible prompt injection",
    "deferred_budget": "Deferred: budget",
    "error": "Error",
}


def issue_ref(issue: dict[str, Any]) -> str:
    """OWNER/REPO#N, or a plain label for the bundled sample issues (number 0)."""
    if int(issue.get("number") or 0) == 0:
        return f"{issue['repo']} (sample issue)"
    return f"{issue['repo']}#{issue['number']}"


def maintainer_summary(report: dict[str, Any]) -> str:
    issue = report["issue"]
    inv = report.get("investigation") or {}
    sort = report.get("sort") or {}
    lines = [
        f"# {issue_ref(issue)}: {issue['title']}",
        "",
        f"Status: **{STATUS_TEXT.get(report['status'], report['status'])}**  ",
        f"Maintainer: {report['maintainer']}  ",
        f"Cost: ${report.get('cost_usd', 0):.6f}",
        "",
    ]
    if sort:
        lines += [
            f"Kind: {sort.get('kind', '?')}, area: {sort.get('area', '?')}, severity: {sort.get('severity', '?')}",
            "",
        ]
    if inv.get("summary"):
        lines += ["## Summary", "", str(inv["summary"]), ""]
    if inv.get("likely_cause"):
        lines += [f"Likely cause: {inv['likely_cause']}", ""]
    repro = report.get("repro")
    if repro:
        lines += ["## Reproduction", "", f"{repro.get('verdict')} ({repro.get('via')})", ""]
    if inv.get("evidence"):
        lines += ["## Evidence", ""] + [f"* {e}" for e in inv["evidence"]] + [""]
    if inv.get("unverified_evidence"):
        lines += ["## Claimed but not backed by a tool call", ""] + [f"* {e}" for e in inv["unverified_evidence"]] + [""]
    if report.get("duplicates"):
        lines += ["## Possible duplicates", ""]
        lines += [f"* #{d['number']} [{d['state']}] {d['title']}" for d in report["duplicates"]]
        if inv.get("duplicate_of"):
            lines += ["", f"Investigator thinks this duplicates #{inv['duplicate_of']}."]
        lines += [""]
    if inv.get("labels"):
        lines += [f"Suggested labels: {', '.join(inv['labels'])}", ""]
    if inv.get("next_step"):
        lines += [f"Next step: {inv['next_step']}", ""]
    if inv.get("reply_draft"):
        lines += ["## Draft reply", "", str(inv["reply_draft"]), ""]
    guard = report.get("guard") or {}
    flags = []
    if guard.get("secrets_redacted"):
        flags.append(f"secrets redacted before the model saw them: {', '.join(guard['secrets_redacted'])}")
    if guard.get("injection_local"):
        flags.append(f"injection phrases: {'; '.join(guard['injection_local'])}")
    for entry in guard.get("injection_gateway") or []:
        flags.append(f"Otari guardrail {entry.get('profile')}: valid={entry.get('valid')} status={entry.get('status')}")
    if guard.get("screen"):
        flags.append(f"reply screen: {guard['screen'].get('verdict')}")
    if guard.get("reply_leaks"):
        flags.append(f"reply leaks blocked: {', '.join(guard['reply_leaks'])}")
    if flags:
        lines += ["## Safety", ""] + [f"* {f}" for f in flags] + [""]
    if report.get("calls"):
        lines += ["## Gateway calls", "", "| Role | Asked for | Served by | Fallback | Cost |", "| --- | --- | --- | --- | --- |"]
        for c in report["calls"]:
            served = f"{c.get('provider') or '?'}:{c.get('model') or '?'}"
            lines.append(f"| {c['role']} | {c['requested']} | {served} | {'yes' if c.get('fallback') else 'no'} | ${c.get('cost_usd', 0):.6f} |")
        lines.append("")
    if report.get("tool_calls"):
        lines += ["## Tools Otari ran", ""]
        for t in report["tool_calls"]:
            what = t.get("url") or t.get("name") or (t.get("code") or "").splitlines()[0:1] or t.get("query") or ""
            if isinstance(what, list):
                what = what[0] if what else ""
            lines.append(f"* {t.get('type')} [{t.get('status')}] {what}")
        lines.append("")
    if report.get("notes"):
        lines += ["## Notes", ""] + [f"* {n}" for n in report["notes"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def draft_comment(report: dict[str, Any]) -> str:
    inv = report.get("investigation") or {}
    reply = str(inv.get("reply_draft") or "").strip()
    if not reply:
        return ""
    footer = "\n\n<sub>Drafted by Triagewell and reviewed by a maintainer before posting.</sub>"
    return reply + footer


FEATURES = [
    ("routing", "Routing and fallbacks"),
    ("roles", "Models in distinct roles"),
    ("fetch", "Web fetch"),
    ("code", "Code execution"),
    ("mcp", "MCP"),
    ("guardrails", "Guardrails"),
    ("user_budget", "Per-user budget"),
    ("limits", "Budgets and spending limits"),
]


def features_used(report: dict[str, Any]) -> dict[str, str]:
    """Which Otari features this one run actually exercised, with the evidence.

    Only what the run's own records show counts: a declared tool the model never
    called is not listed."""
    used: dict[str, str] = {}
    calls = report.get("calls") or []
    policies = [c for c in calls if c.get("requested", "").startswith("triagewell-")]
    if policies:
        fb = [c for c in policies if c.get("fallback")]
        used["routing"] = f"{len(policies)} call(s) through routing policies" + (f", {len(fb)} served by a fallback" if fb else "")
    models = {c.get("model") or c.get("requested") for c in calls}
    roles = {c.get("role") for c in calls}
    if len(roles) >= 2 and len(models) >= 2:
        used["roles"] = f"{len(roles)} roles on {len(models)} models"
    tools = report.get("tool_calls") or []
    fetches = [t for t in tools if t.get("type") == "web_fetch" and t.get("status") == "completed"]
    if fetches:
        used["fetch"] = f"{len(fetches)} page(s) fetched"
    code_runs = [t for t in tools if t.get("type") == "code_execution"]
    repro = report.get("repro") or {}
    if repro.get("via", "").startswith("otari") or code_runs:
        used["code"] = "snippet run in the sandbox" if repro.get("via", "").startswith("otari") else f"{len(code_runs)} model-driven run(s)"
    mcp = [t for t in tools if t.get("type") == "mcp"]
    if mcp:
        used["mcp"] = f"{len(mcp)} call(s) to {mcp[0].get('server', 'MCP')}"
    guard = report.get("guard") or {}
    evaluated = [g for g in guard.get("injection_gateway") or [] if g.get("status") == "evaluated"]
    if evaluated:
        used["guardrails"] = "Otari prompt-injection check " + ("flagged the issue" if any(g.get("valid") is False for g in evaluated) else "passed")
    elif guard.get("injection_gateway"):
        used["guardrails"] = "Otari guardrail requested (service unavailable); local checks applied"
    if calls:
        used["user_budget"] = f"billed to end user '{report.get('maintainer')}'"
    budget = report.get("budget") or {}
    if budget.get("otari_refusal"):
        used["limits"] = f"Otari refused: {budget['otari_refusal'].get('scope')} budget spent"
    elif report.get("status") == "deferred_budget":
        used["limits"] = "stopped by the local daily limit"
    elif calls:
        used["limits"] = f"capped output tokens; ${budget.get('local_spent_today_usd', 0):.4f} of ${budget.get('local_daily_limit_usd', 0):.2f} used before this run"
    return used
