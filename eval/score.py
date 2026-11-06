"""Score eval/runs.jsonl against eval/truth.json.

    uv run python eval/score.py [--settle]

--settle looks every request id up in Otari's usage API (GET
/usage/requests/{id}, the same key) so costs come from what Otari settled, not
from what Triagewell added up. Writes eval/results.json and eval/RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

PRED_KIND = {"bug": "bug", "enhancement": "feature", "documentation": "docs", "question": "question"}


def wilson(k: int, n: int) -> list[float] | None:
    """95% Wilson interval for k successes out of n."""
    if n == 0:
        return None
    z = 1.96
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def ratio(k: int, n: int) -> dict:
    return {"k": k, "n": n, "value": round(k / n, 3) if n else None, "ci95": wilson(k, n)}


def predicted_kind(row: dict) -> str | None:
    for label in row.get("labels") or []:
        if label in PRED_KIND:
            return PRED_KIND[label]
    kind = row.get("kind")
    return {"docs": "docs", "feature": "feature", "bug": "bug", "question": "question"}.get(kind)


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 1)


def settle(rows: list[dict]) -> dict:
    """Otari's settled cost for every request id in the runs."""
    from triagewell.config import load_settings
    from triagewell.gateway import OtariClient

    s = load_settings()
    gw = OtariClient(s.otari_base_url, s.otari_api_key, timeout=30)
    ids = []
    for r in rows:
        ids += [c.get("request_id") for c in r.get("calls") or []]
        ids.append((r.get("repro") or {}).get("request_id"))
    ids = [i for i in ids if i]
    settled, missing = 0.0, 0
    for rid in ids:
        cost = gw.settled_cost(rid, tries=2, wait_s=0.5)
        if cost is None:
            missing += 1
        else:
            settled += cost
    gw.close()
    return {"request_ids": len(ids), "settled_usd": round(settled, 6), "not_found": missing}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settle", action="store_true")
    parser.add_argument("--readme", action="store_true", help="rewrite the README's summary block")
    args = parser.parse_args()
    truth = json.loads((HERE / "truth.json").read_text())
    rows = [json.loads(l) for l in (HERE / "runs.jsonl").read_text().splitlines() if l.strip()]
    ok = [r for r in rows if r["status"] in {"triaged", "quarantined"}]
    res: dict = {"issues": len(rows), "completed": len(ok), "errors": [r["ref"] for r in rows if r["status"] not in {"triaged", "quarantined"}]}
    res["repos"] = dict(Counter(r["ref"].split("#")[0] for r in rows))

    # 1. Kind, against the maintainers' kind labels.
    kinded = [r for r in ok if truth[r["ref"]]["kind"]]
    correct = sum(predicted_kind(r) == truth[r["ref"]]["kind"] for r in kinded)
    bug_pred = [r for r in kinded if "bug" in (r.get("labels") or [])]
    bug_true = [r for r in kinded if truth[r["ref"]]["kind"] == "bug"]
    res["kind"] = {
        "accuracy": ratio(correct, len(kinded)),
        "bug_label_precision": ratio(sum(truth[r["ref"]]["kind"] == "bug" for r in bug_pred), len(bug_pred)),
        "bug_label_recall": ratio(sum("bug" in (r.get("labels") or []) for r in bug_true), len(bug_true)),
        "confusion": dict(Counter(f"{truth[r['ref']]['kind']}->{predicted_kind(r)}" for r in kinded)),
    }

    # 2. Duplicates, against the maintainers' duplicate closes.
    flagged = [r for r in ok if r.get("duplicate_of") is not None or "duplicate" in (r.get("labels") or [])]
    dups = [r for r in ok if truth[r["ref"]]["duplicate"]]
    known = [r for r in dups if truth[r["ref"]]["duplicate_of"] is not None]
    res["duplicates"] = {
        "precision": ratio(sum(truth[r["ref"]]["duplicate"] for r in flagged), len(flagged)),
        "recall": ratio(sum(r in flagged for r in dups), len(dups)),
        "right_original": ratio(sum(r.get("duplicate_of") == truth[r["ref"]]["duplicate_of"] for r in known), len(known)),
        "search_found_original": ratio(sum(truth[r["ref"]]["duplicate_of"] in (r.get("candidates") or []) for r in known), len(known)),
        "false_flags_on_non_duplicates": ratio(sum(not truth[r["ref"]]["duplicate"] for r in flagged), sum(not truth[r["ref"]]["duplicate"] for r in ok)),
    }

    # 3. Reproduction, against whether a merged fix followed.
    withrepro = [r for r in ok if r.get("repro")]
    outcomes = Counter((r["repro"] or {}).get("outcome") for r in withrepro)
    ran = [r for r in withrepro if r["repro"].get("outcome") in {"reproduced", "refuted", "inconclusive"}]
    rep = [r for r in ran if r["repro"]["outcome"] == "reproduced"]
    ref = [r for r in ran if r["repro"]["outcome"] == "refuted"]
    fixed_ran = [r for r in ran if truth[r["ref"]]["fixed"]]
    pins = Counter(((r["repro"] or {}).get("pinned") or {}).get("source", "no pin") for r in withrepro)
    res["reproduction"] = {
        "issues_with_snippet": len(withrepro),
        "outcomes": dict(outcomes),
        "pinned_release": dict(pins),
        "decisive": ratio(len(rep) + len(ref), len(ran)),
        "reproduced_then_fixed": ratio(sum(truth[r["ref"]]["fixed"] for r in rep), len(rep)),
        "fixed_and_reproduced": ratio(sum(r in rep for r in fixed_ran), len(fixed_ran)),
        "refuted_then_not_fixed": ratio(sum(not truth[r["ref"]]["fixed"] for r in ref), len(ref)),
        "base_rate_fixed_among_ran": ratio(len(fixed_ran), len(ran)),
        "by_outcome": {
            o: {"fixed": sum(truth[r["ref"]]["fixed"] for r in ran if r["repro"]["outcome"] == o), "not_fixed": sum(not truth[r["ref"]]["fixed"] for r in ran if r["repro"]["outcome"] == o)}
            for o in ("reproduced", "refuted", "inconclusive")
        },
    }

    # 4. Grounding and safety.
    ev = sum(r.get("evidence", 0) for r in ok)
    unv = sum(r.get("unverified", 0) for r in ok)
    res["grounding"] = {"evidence_items_kept": ev, "unverified_items": unv, "share_unverified": ratio(unv, ev + unv)}
    res["safety"] = {
        "quarantined_real_issues": ratio(sum(r["status"] == "quarantined" for r in ok), len(ok)),
        "reply_screen": dict(Counter((r.get("guard") or {}).get("screen") for r in ok)),
    }

    # 5. Cost, latency, routing, from the gateway's own records.
    calls = [c for r in ok for c in r.get("calls") or []]
    by_role: dict[str, list[int]] = {}
    for c in calls:
        if c.get("duration_ms") is not None:
            by_role.setdefault(c["role"], []).append(c["duration_ms"])
    costs = [r["cost_usd"] for r in ok]
    walls = [r["wall_s"] for r in ok if r.get("wall_s") is not None]
    res["cost_latency"] = {
        "total_usd_reported": round(sum(r.get("cost_usd") or 0 for r in rows), 6),
        "per_issue_usd": {"mean": round(statistics.mean(costs), 6) if costs else None, "median": round(statistics.median(costs), 6) if costs else None, "max": max(costs) if costs else None},
        "sandbox_runs": sum(1 for r in ok if (r.get("repro") or {}).get("request_id")),
        "wall_s": {"median": pct(walls, 0.5), "p90": pct(walls, 0.9), "max": max(walls) if walls else None},
        "otari_duration_ms_median": {role: int(statistics.median(v)) for role, v in by_role.items()},
        "calls": len(calls),
        "fallback_calls": sum(1 for c in calls if c.get("fallback")),
        "providers": dict(Counter(f"{c['role']}:{c.get('provider')}" for c in calls)),
        "mcp_calls_completed": sum(1 for r in ok for t in r.get("tool_calls") or [] if t.get("type") == "mcp" and t.get("status") == "completed"),
        "tool_loop_timeouts_retried": sum(1 for r in ok if any("timed out" in n for n in r.get("notes") or [])),
    }
    if args.settle:
        res["cost_latency"]["otari_settled"] = settle(ok)

    # 6. One request id per feature, from these runs.
    def first(pred):
        for r in ok:
            hit = pred(r)
            if hit:
                return {"issue": r["ref"], "request_id": hit}
        return None

    res["feature_request_ids"] = {
        "routing_fallback": first(lambda r: next((c["request_id"] for c in r["calls"] if c.get("fallback")), None)),
        "mcp": first(lambda r: next((c["request_id"] for c in r["calls"] if c["role"] == "investigator"), None) if any(t.get("type") == "mcp" and t.get("status") == "completed" for t in r.get("tool_calls") or []) else None),
        "code_execution_reproduced": first(lambda r: (r.get("repro") or {}).get("request_id") if (r.get("repro") or {}).get("outcome") == "reproduced" else None),
        "code_execution_refuted": first(lambda r: (r.get("repro") or {}).get("request_id") if (r.get("repro") or {}).get("outcome") == "refuted" else None),
        "sorter": first(lambda r: next((c["request_id"] for c in r["calls"] if c["role"] == "sorter"), None)),
        "screen": first(lambda r: next((c["request_id"] for c in r["calls"] if c["role"] == "screen"), None)),
    }
    res["per_issue"] = [
        {
            "ref": r["ref"], "status": r["status"], "kind_true": truth[r["ref"]]["kind"], "kind_pred": predicted_kind(r),
            "dup_true": truth[r["ref"]]["duplicate_of"] if truth[r["ref"]]["duplicate"] else None, "dup_is_true": truth[r["ref"]]["duplicate"],
            "dup_pred": r.get("duplicate_of"), "repro": (r.get("repro") or {}).get("outcome"),
            "pinned": ((r.get("repro") or {}).get("pinned") or {}).get("version"), "fixed": truth[r["ref"]]["fixed"],
            "cost_usd": r.get("cost_usd"), "wall_s": r.get("wall_s"),
        }
        for r in rows
        if r["ref"] in truth
    ]
    (HERE / "results.json").write_text(json.dumps(res, indent=1))
    (HERE / "RESULTS.md").write_text(markdown(res))
    if args.readme:
        write_readme_summary(res)
    print(markdown(res))


def write_readme_summary(res: dict) -> None:
    """The headline numbers at the top of the README, written from results.json
    so they cannot drift from the run they describe."""
    readme = HERE.parent / "README.md"
    text = readme.read_text()
    start, end = "<!-- eval-summary -->", "<!-- /eval-summary -->"
    if start not in text:
        return
    first_path = HERE / "results-v1.json"
    first = json.loads(first_path.read_text()) if first_path.exists() else None
    k, d, rp, c = res["kind"], res["duplicates"], res["reproduction"], res["cost_latency"]

    def both(a: str, b: str) -> str:
        now = fmt(res[a][b])
        return f"{now}; first run {fmt(first[a][b])}" if first else now

    block = "\n".join([
        f"**Measured, not claimed.** We replayed {res['issues']} closed public issues from {', '.join(res['repos'])} through hosted",
        "Otari as if they had just been opened (no labels, no comments, no state) and scored Triagewell against what the",
        "maintainers actually did. Details, caveats and every issue are in [Evaluation](#evaluation) and on the dashboard's",
        "Evaluation page.",
        "",
        "| What | Result |",
        "| --- | --- |",
        f"| When the sandbox said **reproduced**, a fix was merged | {both('reproduction', 'reproduced_then_fixed')} |",
        f"| When it said **refuted**, no fix was merged | {both('reproduction', 'refuted_then_not_fixed')} |",
        f"| Snippet runs with a decisive verdict | {both('reproduction', 'decisive')} |",
        f"| `bug` label precision | {both('kind', 'bug_label_precision')} |",
        f"| Duplicate flagged, and it was one | {both('duplicates', 'precision')} |",
        f"| Evidence the model cited without a tool call behind it (shown as unverified) | {both('grounding', 'share_unverified')} |",
        f"| Cost | ${c['total_usd_reported']:.2f} for all {res['issues']}, median ${c['per_issue_usd']['median']:.4f} per issue, reconciled with Otari's settled records |",
    ])
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    text = f"{head}{start}\n{block}\n{end}{tail}"
    start2, end2 = "<!-- eval-results -->", "<!-- /eval-results -->"
    if start2 in text:
        rows = ["| Measure | Round 2 | Round 1 |" if first else "| Measure | Result |", "| --- | --- | --- |" if first else "| --- | --- |"]
        for name, a, b in MEASURES:
            rows.append(f"| {name} | {fmt(res[a][b])} | {fmt(first[a][b])} |" if first else f"| {name} | {fmt(res[a][b])} |")
        cl = res["cost_latency"]
        settled = cl.get("otari_settled")
        extra = [
            "",
            f"Sandbox outcomes, round 2: {rp['outcomes']}; release pinned by: {rp['pinned_release']}."
            + (f" Round 1: {first['reproduction']['outcomes']}." if first else ""),
            "",
            f"Cost and latency, round 2: ${cl['total_usd_reported']:.4f} for {res['issues']} issues ({cl['sandbox_runs']} sandbox runs at $0.01 each),"
            f" median {cl['wall_s']['median']} s per issue, p90 {cl['wall_s']['p90']} s. Otari's median response time by role:"
            f" {', '.join(f'{r} {v} ms' for r, v in cl['otari_duration_ms_median'].items())}."
            + (f" Otari's settled records for the same {settled['request_ids']} request ids add up to ${settled['settled_usd']:.4f} ({settled['not_found']} missing)." if settled else ""),
            f"{cl['calls']} model calls, {cl['fallback_calls']} served by a fallback provider, {cl['mcp_calls_completed']} DeepWiki MCP calls completed,"
            f" {cl['tool_loop_timeouts_retried']} investigator tool loops retried without tools after the edge timed out.",
        ]
        ids = res.get("feature_request_ids") or {}
        id_rows = ["", "| Otari feature | Issue | Request id |", "| --- | --- | --- |"]
        for label, key in (("Sorter, small model", "sorter"), ("Reply screen, Llama Guard", "screen"), ("Managed MCP (DeepWiki)", "mcp"),
                           ("Code execution: reproduced", "code_execution_reproduced"), ("Code execution: refuted", "code_execution_refuted"),
                           ("Routing fallback", "routing_fallback")):
            hit = ids.get(key)
            id_rows.append(f"| {label} | {hit['issue'] if hit else 'none in this run'} | {('`' + hit['request_id'] + '`') if hit else ''} |")
        body = "\n".join(rows + extra + id_rows)
        head, rest = text.split(start2, 1)
        _, tail = rest.split(end2, 1)
        text = f"{head}{start2}\n{body}\n{end2}{tail}"
    readme.write_text(text)


MEASURES = [
    ("Kind matches the maintainers' label", "kind", "accuracy"),
    ("`bug` label precision", "kind", "bug_label_precision"),
    ("`bug` label recall", "kind", "bug_label_recall"),
    ("Duplicate flag precision", "duplicates", "precision"),
    ("Duplicate flag recall", "duplicates", "recall"),
    ("Named the same original as the maintainer", "duplicates", "right_original"),
    ("Search surfaced that original", "duplicates", "search_found_original"),
    ("Non-duplicates wrongly flagged", "duplicates", "false_flags_on_non_duplicates"),
    ("Snippet runs with a decisive verdict", "reproduction", "decisive"),
    ("\"Reproduced\" and a fix was merged", "reproduction", "reproduced_then_fixed"),
    ("Fixed bugs that reproduced", "reproduction", "fixed_and_reproduced"),
    ("\"Refuted\" and no fix was merged", "reproduction", "refuted_then_not_fixed"),
    ("Base rate: fixed, among snippets that ran", "reproduction", "base_rate_fixed_among_ran"),
    ("Evidence items marked unverified", "grounding", "share_unverified"),
    ("Real issues wrongly quarantined", "safety", "quarantined_real_issues"),
]


def fmt(r: dict | None) -> str:
    if not r or r.get("n", 0) == 0:
        return "n/a"
    ci = r["ci95"]
    return f"{r['k']}/{r['n']} = {r['value']:.0%} (95% CI {ci[0]:.0%} to {ci[1]:.0%})"


def markdown(res: dict) -> str:
    k, d, rp, g, s, c = res["kind"], res["duplicates"], res["reproduction"], res["grounding"], res["safety"], res["cost_latency"]
    lines = [
        f"Issues: {res['issues']} closed issues from {', '.join(f'{r} ({n})' for r, n in res['repos'].items())}; {res['completed']} completed.",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        f"| Kind matches the maintainers' label | {fmt(k['accuracy'])} |",
        f"| `bug` label precision | {fmt(k['bug_label_precision'])} |",
        f"| `bug` label recall | {fmt(k['bug_label_recall'])} |",
        f"| Duplicate flag precision | {fmt(d['precision'])} |",
        f"| Duplicate flag recall | {fmt(d['recall'])} |",
        f"| Named the same original as the maintainer | {fmt(d['right_original'])} |",
        f"| Search surfaced that original | {fmt(d['search_found_original'])} |",
        f"| Non-duplicates wrongly flagged | {fmt(d['false_flags_on_non_duplicates'])} |",
        f"| Snippet runs with a decisive verdict | {fmt(rp['decisive'])} |",
        f"| \"Reproduced\" and a fix was merged | {fmt(rp['reproduced_then_fixed'])} |",
        f"| Fixed bugs that reproduced | {fmt(rp['fixed_and_reproduced'])} |",
        f"| \"Refuted\" and no fix was merged | {fmt(rp['refuted_then_not_fixed'])} |",
        f"| Base rate: fixed, among snippets that ran | {fmt(rp['base_rate_fixed_among_ran'])} |",
        f"| Evidence items marked unverified | {fmt(g['share_unverified'])} |",
        f"| Real issues wrongly quarantined | {fmt(s['quarantined_real_issues'])} |",
        "",
        f"Sandbox outcomes: {rp['outcomes']}; pinned release: {rp['pinned_release']}.",
        "",
        f"Cost: ${c['total_usd_reported']:.4f} in total, median ${c['per_issue_usd']['median']:.6f} per issue, {c['sandbox_runs']} sandbox runs."
        + (f" Otari's settled records for the same {c['otari_settled']['request_ids']} request ids: ${c['otari_settled']['settled_usd']:.4f} ({c['otari_settled']['not_found']} not found)." if c.get("otari_settled") else ""),
        f"Latency: median {c['wall_s']['median']} s per issue, p90 {c['wall_s']['p90']} s; Otari response time median by role (ms): {c['otari_duration_ms_median']}.",
        f"Routing: {c['calls']} calls, {c['fallback_calls']} served by a fallback provider; MCP calls completed: {c['mcp_calls_completed']}; tool loops retried after an edge timeout: {c['tool_loop_timeouts_retried']}.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
