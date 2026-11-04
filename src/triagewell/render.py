"""Turn a report into Markdown: the maintainer summary and the draft comment."""

from __future__ import annotations

from typing import Any

STATUS_TEXT = {
    "triaged": "Triaged",
    "quarantined": "Quarantined: possible prompt injection",
    "deferred_budget": "Deferred: budget",
    "error": "Error",
}


def maintainer_summary(report: dict[str, Any]) -> str:
    issue = report["issue"]
    inv = report.get("investigation") or {}
    sort = report.get("sort") or {}
    lines = [
        f"# {issue['repo']}#{issue['number']}: {issue['title']}",
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
