"""A small server-rendered dashboard. Binds to 127.0.0.1 by default."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .config import Settings, load_settings
from .gateway import GatewayError
from .ledger import Ledger
from .render import FEATURES, STATUS_TEXT, features_used, issue_ref
from .service import budget_status, list_samples, triage
from .triage import load_report, load_reports

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
EVAL_RESULTS = Path(__file__).resolve().parent.parent.parent / "eval" / "results.json"

CSS = """
:root{--bg:#F4F9FF;--surface:#FFFFFF;--primary:#2563EB;--primary-soft:#E8F0FF;--text:#0B1B33;--muted:#4A5B78;
--line:#D9E6FA;--accent:#4C1D95;--accent-soft:#EFE9FB;--danger:#9F1239;--danger-soft:#FDECF1;--ok:#166534;--ok-soft:#E7F6EC}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--surface);border-bottom:1px solid var(--line)}
.bar{max-width:1080px;margin:0 auto;padding:14px 16px;display:flex;align-items:center;gap:12px}
.bar img{width:34px;height:34px}.brand{font-weight:700;font-size:18px;color:var(--text)}
.bar .sub{color:var(--muted);font-size:13px}.bar nav{margin-left:auto;display:flex;gap:16px;font-size:14px}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(11,27,51,.04)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
label{display:block;font-size:13px;font-weight:600;color:var(--muted);margin-bottom:6px}
input,select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font:inherit;background:#fff;color:var(--text)}
input:focus,select:focus{outline:2px solid var(--primary);outline-offset:1px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}.row>div{flex:1 1 200px}
button{background:var(--primary);color:#fff;border:0;border-radius:10px;padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}.or{color:var(--muted);font-size:13px;padding-bottom:10px;flex:0 0 auto}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.s-triaged{background:var(--ok-soft);color:var(--ok)}.s-quarantined{background:var(--danger-soft);color:var(--danger)}
.s-deferred_budget{background:var(--accent-soft);color:var(--accent)}.s-error{background:var(--danger-soft);color:var(--danger)}
.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
pre{background:#0B1B33;color:#E6EEFF;padding:12px;border-radius:10px;overflow:auto;max-height:280px;font-size:12.5px;white-space:pre-wrap;word-break:break-word}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.feat{border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.feat.on{border-color:var(--primary);background:var(--primary-soft)}.feat b{display:block;font-size:13px}
.feat span{font-size:12.5px;color:var(--muted)}.feat.on span{color:var(--text)}
.meter{height:8px;background:var(--primary-soft);border-radius:99px;overflow:hidden;margin-top:6px}
.meter i{display:block;height:100%;background:var(--primary)}.meter.hot i{background:var(--danger)}
.alert{background:var(--danger-soft);color:var(--danger);border-radius:10px;padding:10px 12px;margin-bottom:14px}
.reply{border-left:4px solid var(--accent);background:var(--accent-soft);padding:12px 14px;border-radius:0 10px 10px 0;white-space:pre-wrap}
ul{margin:0;padding-left:20px}.kv{display:grid;grid-template-columns:140px 1fr;gap:6px 12px;font-size:14px}
.tablewrap{overflow-x:auto}
@media (max-width:640px){.bar .sub{display:none}.kv{grid-template-columns:1fr}h1{font-size:20px}}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"><style>{CSS}</style></head><body>
<header><div class="bar"><a href="/"><img src="/assets/logo.svg" alt=""></a>
<div><a class="brand" href="/">Triagewell</a><div class="sub">Issue triage through Otari, on open-weight models</div></div>
<nav><a href="/">Triage</a><a href="/eval">Evaluation</a><a href="/budgets">Budgets</a></nav></div></header>
<main>{body}</main></body></html>"""
    return HTMLResponse(doc)


def status_chip(status: str) -> str:
    return f'<span class="chip s-{esc(status)}">{esc(STATUS_TEXT.get(status, status))}</span>'


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    return urlparse(origin).netloc == request.headers.get("host")


def create_app(env_file: str | None = None, settings: Settings | None = None) -> Starlette:
    settings = settings or load_settings(env_file)

    def home(request: Request, error: str | None = None, form: dict[str, str] | None = None) -> HTMLResponse:
        form = form or {}
        samples = "".join(f'<option value="{esc(s)}"{" selected" if form.get("sample") == s else ""}>{esc(s)}</option>' for s in list_samples())
        rows = []
        for r in load_reports(settings.home, limit=25):
            issue = r["issue"]
            rows.append(
                f"<tr><td>{status_chip(r['status'])}</td><td><a href=\"/report/{esc(r['id'])}\">{esc(issue_ref(issue))}</a>"
                f"<div class=\"muted\">{esc(issue['title'][:90])}</div></td><td>{esc(r['maintainer'])}</td>"
                f"<td class=\"mono\">${r.get('cost_usd', 0):.6f}</td><td class=\"muted\">{esc(r['started_at'][5:10])} {esc(r['started_at'][11:16])} UTC</td></tr>"
            )
        table = (
            '<div class="tablewrap"><table><thead><tr><th>Status</th><th>Issue</th><th>Maintainer</th><th>Cost</th><th>Time</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table></div>"
            if rows
            else '<p class="muted">No issues triaged yet.</p>'
        )
        alert = f'<div class="alert">{esc(error)}</div>' if error else ""
        posting = "on for " + ", ".join(settings.post_repos) if settings.allow_post else "off: drafts go to the local outbox"
        body = f"""
<h1>Triage an issue</h1><p class="muted">Gateway: <span class="mono">{esc(settings.otari_base_url)}</span>. Posting to GitHub is {esc(posting)}.</p>
<div class="card">{alert}
<form method="post" action="/triage" id="tf">
<div class="row"><div><label for="ref">GitHub issue</label><input id="ref" name="ref" placeholder="owner/repo#123 or issue URL" value="{esc(form.get('ref', ''))}"></div>
<div class="or">or</div>
<div><label for="sample">Bundled sample</label><select id="sample" name="sample"><option value="">(none)</option>{samples}</select></div>
<div><label for="maintainer">Maintainer</label><input id="maintainer" name="maintainer" value="{esc(form.get('maintainer', 'alice'))}" required></div>
<div><label for="budget">New maintainer's budget</label><input id="budget" name="budget" placeholder="key default" value="{esc(form.get('budget', ''))}"></div>
<div style="flex:0 0 auto"><button id="go" type="submit">Triage</button></div></div>
<p class="muted" id="wait" hidden>Working: sorting, searching for duplicates, running the snippet, investigating. This takes 20 to 90 seconds.</p>
</form></div>
<div class="card"><h2>Recent triage</h2>{table}</div>
<script>document.getElementById('tf').addEventListener('submit',function(){{var b=document.getElementById('go');b.disabled=true;b.textContent='Triaging';document.getElementById('wait').hidden=false;}});</script>
"""
        return page("Triagewell", body)

    async def index(request: Request) -> Response:
        return home(request)

    async def run_triage(request: Request) -> Response:
        if not _same_origin(request):
            return Response("Cross-origin request refused", status_code=403)
        form = await request.form()
        data = {k: str(form.get(k) or "").strip() for k in ("ref", "sample", "maintainer", "budget")}
        try:
            report = await run_in_threadpool(
                triage, settings, ref=data["ref"] or None, sample=None if data["ref"] else data["sample"] or None, maintainer=data["maintainer"], budget=data["budget"] or None
            )
        except (ValueError, RuntimeError, GatewayError) as exc:
            return home(request, error=str(exc), form=data)
        except Exception as exc:  # show network trouble instead of a stack trace
            return home(request, error=f"{type(exc).__name__}: {exc}", form=data)
        return RedirectResponse(f"/report/{report['id']}", status_code=303)

    async def report_page(request: Request) -> Response:
        report = load_report(settings.home, request.path_params["rid"])
        if report is None:
            return page("Not found", '<div class="card">No such report.</div>')
        return page(f"Triagewell: {issue_ref(report['issue'])}", render_report(report))

    async def report_json(request: Request) -> Response:
        report = load_report(settings.home, request.path_params["rid"])
        return JSONResponse(report) if report else JSONResponse({"detail": "not found"}, status_code=404)

    async def budgets(request: Request) -> Response:
        ledger = Ledger(settings.home)
        spent = ledger.by_maintainer()
        names = sorted(set(spent) | {r["maintainer"] for r in ledger.runs(limit=200)})
        cards = []
        for name in names:
            status = await run_in_threadpool(budget_status, settings, name)
            local = status["local"]
            otari = status["otari"]
            limit = (status.get("otari_budget") or {}).get("max_budget")
            if otari:
                spent = float(otari.get("spend") or 0)
                pct = min(100, int(100 * spent / limit)) if limit else 0
                resets = str(otari.get("next_budget_reset_at") or "")[:16].replace("T", " ")
                head = f'Otari budget <span class="mono">{esc(otari.get("budget_id"))}</span>: ${spent:.6f} of ' + (f"${limit:g}" if limit else "no dollar limit")
                otari_html = (
                    f'<div class="kv"><div class="muted">Requests</div><div class="mono">{esc(otari.get("current_requests"))}</div>'
                    f'<div class="muted">Resets</div><div class="mono">{esc(resets)} UTC</div>'
                    f'<div class="muted">Blocked</div><div>{"yes" if otari.get("blocked") else "no"}</div>'
                    f'<div class="muted">Local ledger</div><div class="mono">${local["spent_today_usd"]:.6f} of ${local["daily_limit_usd"]:.2f}</div></div>'
                )
            else:
                pct = min(100, int(100 * local["spent_today_usd"] / local["daily_limit_usd"])) if local["daily_limit_usd"] else 0
                head = f'Local ledger: ${local["spent_today_usd"]:.6f} of ${local["daily_limit_usd"]:.2f} today'
                otari_html = f'<p class="muted">Otari end-user record: {esc(status["otari_error"] or "not created yet")}</p>'
            cards.append(
                f'<div class="card"><h2>{esc(name)}</h2><div class="muted">{head}</div>'
                f'<div class="meter{" hot" if pct >= 80 else ""}"><i style="width:{pct}%"></i></div>'
                f'<p class="muted" style="margin:6px 0 12px">{pct}% used</p>{otari_html}</div>'
            )
        body = (
            "<h1>Budgets</h1><p class=\"muted\">Each maintainer is an Otari end user on a service key, with a daily budget of their own; "
            "the key's ceiling caps the team. Triagewell also refuses to start an issue when a maintainer has less left than one issue may cost.</p>"
            + ('<div class="grid">' + "".join(cards) + "</div>" if cards else '<div class="card muted">No runs yet.</div>')
        )
        return page("Triagewell: budgets", body)

    async def evaluation(request: Request) -> Response:
        return page("Triagewell: evaluation", render_eval(EVAL_RESULTS))

    async def asset(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in {"logo.svg", "logo-512.png", "favicon.svg"}:
            return Response(status_code=404)
        return FileResponse(ASSETS / name)

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/triage", run_triage, methods=["POST"]),
            Route("/report/{rid}.json", report_json),
            Route("/report/{rid}", report_page),
            Route("/budgets", budgets),
            Route("/eval", evaluation),
            Route("/assets/{name}", asset),
            Route("/healthz", healthz),
        ]
    )


def render_report(report: dict[str, Any]) -> str:
    issue = report["issue"]
    inv = report.get("investigation") or {}
    sort = report.get("sort") or {}
    used = features_used(report)
    feats = "".join(
        f'<div class="feat{" on" if key in used else ""}"><b>{esc(label)}</b><span>{esc(used.get(key, "not used in this run"))}</span></div>'
        for key, label in FEATURES
    )
    link = f'<a href="{esc(issue.get("url"))}" rel="noreferrer">{esc(issue_ref(issue))}</a>' if issue.get("url") else esc(issue_ref(issue))
    parts = [
        f'<p class="muted">{link} by {esc(issue.get("author"))}. Triaged for {esc(report["maintainer"])}. Cost ${report.get("cost_usd", 0):.6f}.</p>',
        f'<h1>{esc(issue["title"])}</h1><p>{status_chip(report["status"])} '
        + (f'<span class="chip" style="background:var(--primary-soft);color:var(--primary)">{esc(sort.get("kind"))} / {esc(sort.get("severity"))}</span> ' if sort.get("kind") else "")
        + "".join(f'<span class="chip" style="background:#fff;border:1px solid var(--line)">{esc(label)}</span> ' for label in inv.get("labels") or [])
        + "</p>",
        f'<div class="card"><h2>Otari features this run used</h2><div class="features">{feats}</div></div>',
    ]
    summary = []
    if inv.get("summary"):
        summary.append(f"<p>{esc(inv['summary'])}</p>")
    if inv.get("likely_cause"):
        summary.append(f'<p><b>Likely cause.</b> {esc(inv["likely_cause"])}</p>')
    if inv.get("next_step"):
        summary.append(f'<p><b>Next step.</b> {esc(inv["next_step"])}</p>')
    if inv.get("duplicate_of"):
        summary.append(f'<p><b>Possible duplicate of</b> #{esc(inv["duplicate_of"])}</p>')
    if report.get("notes"):
        summary.append("<ul>" + "".join(f'<li class="muted">{esc(n)}</li>' for n in report["notes"]) + "</ul>")
    parts.append(f'<div class="card"><h2>Summary</h2>{"".join(summary) or "<p class=muted>No summary.</p>"}</div>')
    if inv.get("reply_draft"):
        parts.append(f'<div class="card"><h2>Draft reply (not posted)</h2><div class="reply">{esc(inv["reply_draft"])}</div></div>')

    grid = []
    repro = report.get("repro")
    if repro:
        block = f'<p><b>{esc(repro.get("verdict"))}</b> <span class="muted">({esc(repro.get("via"))})</span></p>'
        pin = repro.get("pinned")
        facts = []
        if pin:
            facts.append(f'<div class="muted">Replayed against</div><div class="mono">{esc(pin.get("package"))}=={esc(pin.get("version"))} ({esc(pin.get("source"))})</div>')
        if repro.get("added_imports"):
            facts.append(f'<div class="muted">Imports added</div><div class="mono">{esc("; ".join(repro["added_imports"]))}</div>')
        if repro.get("added_packages"):
            facts.append(f'<div class="muted">Packages added</div><div class="mono">{esc(", ".join(repro["added_packages"]))}</div>')
        if repro.get("request_id"):
            facts.append(f'<div class="muted">Otari request</div><div class="mono">{esc(repro["request_id"])}, ${float(repro.get("cost_usd") or 0):.2f}</div>')
        if facts:
            block += f'<div class="kv" style="margin-bottom:10px">{"".join(facts)}</div>'
        if repro.get("code"):
            block += f"<pre>{esc(repro['code'])}</pre>"
        if repro.get("stderr") or repro.get("stdout"):
            block += f"<pre>{esc((repro.get('stdout') or '') + (repro.get('stderr') or ''))[-1500:]}</pre>"
        grid.append(f'<div class="card"><h2>Sandbox run</h2>{block}</div>')
    ev = "".join(f"<li>{esc(e)}</li>" for e in inv.get("evidence") or [])
    unv = "".join(f"<li>{esc(e)}</li>" for e in inv.get("unverified_evidence") or [])
    if ev or unv:
        grid.append(
            '<div class="card"><h2>Evidence</h2>'
            + (f"<ul>{ev}</ul>" if ev else "")
            + (f'<p class="muted" style="margin-top:12px">Claimed by the model, not backed by any tool call:</p><ul class="muted">{unv}</ul>' if unv else "")
            + "</div>"
        )
    dups = "".join(f'<li><a href="{esc(d["url"])}" rel="noreferrer">#{esc(d["number"])}</a> [{esc(d["state"])}] {esc(d["title"])}</li>' for d in report.get("duplicates") or [])
    if dups:
        grid.append(f'<div class="card"><h2>Similar issues (GitHub search)</h2><ul>{dups}</ul></div>')
    guard = report.get("guard") or {}
    g = []
    if guard.get("secrets_redacted"):
        g.append(f"<li>Redacted before any model saw the issue: {esc(', '.join(guard['secrets_redacted']))}</li>")
    if guard.get("injection_local"):
        g.append("<li>Injection phrases: " + esc("; ".join(guard["injection_local"])) + "</li>")
    for entry in guard.get("injection_gateway") or []:
        g.append(f"<li>Otari guardrail <span class=\"mono\">{esc(entry.get('profile'))}</span>: valid={esc('n/a' if entry.get('valid') is None else entry.get('valid'))}, status={esc(entry.get('status'))}</li>")
    if guard.get("screen"):
        g.append(f"<li>Reply screen ({esc(guard['screen'].get('model', ''))}): {esc(guard['screen'].get('verdict'))}</li>")
    if guard.get("reply_leaks"):
        g.append(f"<li>Secret-shaped text removed from the reply: {esc(', '.join(guard['reply_leaks']))}</li>")
    grid.append(f'<div class="card"><h2>Safety</h2>{"<ul>" + "".join(g) + "</ul>" if g else "<p class=muted>Nothing flagged.</p>"}</div>')
    parts.append('<div class="grid">' + "".join(grid) + "</div>")

    rows = "".join(
        f"<tr><td>{esc(c['role'])}</td><td class=\"mono\">{esc(c['requested'])}</td><td class=\"mono\">{esc(c.get('provider'))}:{esc(c.get('model'))}</td>"
        f"<td>{'yes (' + esc(c.get('attempts')) + ' attempts)' if c.get('fallback') else 'no'}</td><td class=\"mono\">${c.get('cost_usd', 0):.6f}</td>"
        f"<td class=\"mono muted\">{esc((c.get('request_id') or '')[:8])}</td></tr>"
        for c in report.get("calls") or []
    )
    if rows:
        parts.append(
            '<div class="card"><h2>Gateway calls</h2><div class="tablewrap"><table><thead><tr><th>Role</th><th>Asked for</th><th>Served by</th><th>Fallback</th><th>Cost</th><th>Request</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></div>"
        )
    tools = []
    for t in report.get("tool_calls") or []:
        what = t.get("url") or (f"{t.get('server')}.{t.get('name')}" if t.get("type") == "mcp" else "") or (t.get("code") or "")[:200]
        detail = t.get("output") or t.get("stderr") or t.get("stdout") or ""
        if t.get("via"):
            detail = f"{t['via']}, request {(t.get('request_id') or '')[:8]}, ${float(t.get('cost_usd') or 0):.2f}"
        tools.append(f"<tr><td>{esc(t.get('type'))}</td><td>{esc(t.get('status'))}</td><td class=\"mono\">{esc(what)}</td><td class=\"muted\">{esc(str(detail)[:240])}</td></tr>")
    if tools:
        parts.append(
            '<div class="card"><h2>Tools Otari ran (<span class="mono">POST /fetch</span> and <span class="mono">otari_tool_calls</span>)</h2><div class="tablewrap"><table><thead><tr><th>Tool</th><th>Status</th><th>Target</th><th>Result</th></tr></thead>'
            f"<tbody>{''.join(tools)}</tbody></table></div></div>"
        )
    parts.append(f'<p class="muted">Report {esc(report["id"])}. <a href="/report/{esc(report["id"])}.json">JSON</a></p>')
    return "".join(parts)


def _fmt_ratio(r: dict[str, Any] | None) -> str:
    if not r or not r.get("n"):
        return '<span class="muted">n/a</span>'
    lo, hi = r["ci95"]
    return f'<b>{r["k"]}/{r["n"]}</b> = {r["value"]:.0%} <span class="muted">(95% CI {lo:.0%} to {hi:.0%})</span>'


def render_eval(path: Path) -> str:
    """The evaluation table: Triagewell's answers on closed issues, scored
    against what the maintainers actually did."""
    if not path.exists():
        return '<h1>Evaluation</h1><div class="card muted">No results yet. Run <span class="mono">eval/run_eval.py</span> then <span class="mono">eval/score.py</span>.</div>'
    res = json.loads(path.read_text())
    first_path = path.with_name("results-v1.json")
    first = json.loads(first_path.read_text()) if first_path.exists() and first_path != path else None
    k, d, rp, g, sf, c = (res[x] for x in ("kind", "duplicates", "reproduction", "grounding", "safety", "cost_latency"))
    keys = [
        ("kind", "accuracy"), ("kind", "bug_label_precision"), ("kind", "bug_label_recall"),
        ("duplicates", "precision"), ("duplicates", "recall"), ("duplicates", "right_original"), ("duplicates", "search_found_original"),
        ("duplicates", "false_flags_on_non_duplicates"), ("reproduction", "decisive"), ("reproduction", "reproduced_then_fixed"),
        ("reproduction", "fixed_and_reproduced"), ("reproduction", "refuted_then_not_fixed"), ("reproduction", "base_rate_fixed_among_ran"),
        ("grounding", "share_unverified"), ("safety", "quarantined_real_issues"),
    ]
    rows = [
        ("Kind matches the maintainers' label", k["accuracy"]),
        ("<span class=mono>bug</span> label precision", k["bug_label_precision"]),
        ("<span class=mono>bug</span> label recall", k["bug_label_recall"]),
        ("Duplicate flag precision", d["precision"]),
        ("Duplicate flag recall", d["recall"]),
        ("Named the same original as the maintainer", d["right_original"]),
        ("Search surfaced that original", d["search_found_original"]),
        ("Non-duplicates wrongly flagged", d["false_flags_on_non_duplicates"]),
        ("Snippet runs with a decisive verdict", rp["decisive"]),
        ("&ldquo;Reproduced&rdquo; and a fix was merged", rp["reproduced_then_fixed"]),
        ("Fixed bugs that reproduced", rp["fixed_and_reproduced"]),
        ("&ldquo;Refuted&rdquo; and no fix was merged", rp["refuted_then_not_fixed"]),
        ("Base rate: fixed, among snippets that ran", rp["base_rate_fixed_among_ran"]),
        ("Evidence items marked unverified", g["share_unverified"]),
        ("Real issues wrongly quarantined", sf["quarantined_real_issues"]),
    ]
    if first:
        head = "<thead><tr><th>Measure</th><th>Round 2 (after fixes)</th><th>Round 1 (first run)</th></tr></thead>"
        summary = head + "".join(
            f"<tr><td>{name}</td><td>{_fmt_ratio(r)}</td><td>{_fmt_ratio(first.get(a, {}).get(b))}</td></tr>"
            for (name, r), (a, b) in zip(rows, keys)
        )
    else:
        summary = "".join(f"<tr><td>{name}</td><td>{_fmt_ratio(r)}</td></tr>" for name, r in rows)
    caveat = (
        '<p class="muted">Round 1 is the first run. Round 2 is after fixing the defects round 1 exposed (search terms split into '
        "letters, an over-strict duplicate search, snippets missing imports, sandbox runs too slow for the gateway edge, crashes "
        "counted when the report described none). Round 2 is measured on the same issues, so read it as optimistic.</p>"
        if first
        else ""
    )
    per = []
    for i in res.get("per_issue") or []:
        owner_repo, number = i["ref"].split("#")
        dup = "" if not i["dup_is_true"] else f"#{i['dup_true']}" if i["dup_true"] else "yes"
        per.append(
            f'<tr><td><a href="https://github.com/{esc(owner_repo)}/issues/{esc(number)}">{esc(i["ref"])}</a></td>'
            f"<td>{esc(i['kind_true'] or '')}</td><td>{esc(i['kind_pred'] or '')}</td>"
            f"<td>{esc(dup)}</td><td>{esc('#' + str(i['dup_pred']) if i['dup_pred'] else '')}</td>"
            f"<td>{esc(i['repro'] or '')}</td><td class=mono>{esc(i['pinned'] or '')}</td><td>{'yes' if i['fixed'] else 'no'}</td>"
            f"<td class=mono>${(i['cost_usd'] or 0):.5f}</td><td class=mono>{esc(i['wall_s'])}</td></tr>"
        )
    settled = c.get("otari_settled")
    cost_line = (
        f"${c['total_usd_reported']:.4f} in total, median ${c['per_issue_usd']['median']:.6f} per issue, {c['sandbox_runs']} sandbox runs"
        + (f"; Otari settled ${settled['settled_usd']:.4f} for the same {settled['request_ids']} request ids" if settled else "")
    )
    return f"""
<h1>Evaluation</h1>
<p class="muted">{res['issues']} closed public issues ({esc(', '.join(f'{r} {n}' for r, n in res['repos'].items()))}), opened in 2025 or later.
Triagewell saw each one as if it had just been opened: no labels, no comments, no state, and a duplicate search limited to older issues.
Its answers are scored against what the maintainers did.</p>
<div class="card"><h2>Scores</h2>{caveat}<div class="tablewrap"><table><tbody>{summary}</tbody></table></div></div>
<div class="card"><h2>Cost and latency, from Otari</h2><div class="kv">
<div class="muted">Cost</div><div class="mono">{esc(cost_line)}</div>
<div class="muted">Per issue</div><div class="mono">median {esc(c['wall_s']['median'])} s, p90 {esc(c['wall_s']['p90'])} s</div>
<div class="muted">Otari time by role</div><div class="mono">{esc(', '.join(f'{r} {v} ms' for r, v in c['otari_duration_ms_median'].items()))}</div>
<div class="muted">Routing</div><div class="mono">{esc(c['calls'])} calls, {esc(c['fallback_calls'])} by a fallback provider, {esc(c['mcp_calls_completed'])} MCP calls</div>
<div class="muted">Sandbox outcomes</div><div class="mono">{esc(rp['outcomes'])}</div></div></div>
<div class="card"><h2>Every issue</h2><div class="tablewrap"><table><thead><tr><th>Issue</th><th>Kind (maintainer)</th><th>Kind (Triagewell)</th>
<th>Duplicate of (maintainer)</th><th>Duplicate of (Triagewell)</th><th>Sandbox</th><th>Pinned</th><th>Fix merged</th><th>Cost</th><th>Seconds</th></tr></thead>
<tbody>{''.join(per)}</tbody></table></div></div>
"""
