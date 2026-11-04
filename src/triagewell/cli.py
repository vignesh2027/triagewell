"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from .config import load_settings
from .gateway import GatewayError, OtariClient
from .github import GitHubReader, load_issue_file, parse_ref
from .ledger import Ledger
from .outbox import PostRefused, post_github, write_local
from .render import maintainer_summary
from .triage import Triager


def _progress(kind: str, data: dict[str, Any]) -> None:
    if kind == "call":
        print(f"  .. {data['role']}: asking {data['model']}", file=sys.stderr)
    elif kind == "called":
        served = f"{data.get('provider') or '?'}:{data.get('model') or '?'}"
        fb = " (fallback)" if data.get("fallback") else ""
        print(f"  ok {data['role']}: served by {served}{fb}, ${data.get('cost_usd', 0):.6f}", file=sys.stderr)
    elif kind == "sorted":
        print(f"  ok sorted as {data.get('kind')} / {data.get('area')} / {data.get('severity')}", file=sys.stderr)
    elif kind == "repro":
        print(f"  ok sandbox: {data.get('verdict')}", file=sys.stderr)
    elif kind == "done":
        print(f"  == {data['status']}, total ${data['cost_usd']:.6f}", file=sys.stderr)


def _clients(settings):
    gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=settings.timeout_s)
    github = GitHubReader(settings.github_token)
    return gateway, github


def cmd_triage(args: argparse.Namespace) -> int:
    settings = load_settings(args.env)
    gateway, github = _clients(settings)
    if args.file:
        issue = load_issue_file(args.file)
    else:
        repo, number = parse_ref(args.ref)
        issue = github.issue(repo, number)
    print(f"Triaging {issue.ref} for {args.maintainer}", file=sys.stderr)
    report = Triager(settings, gateway, github, on_event=_progress).run(issue, args.maintainer).to_dict()
    _emit(settings, report, args)
    return 0 if report["status"] in {"triaged", "quarantined", "deferred_budget"} else 1


def cmd_scan(args: argparse.Namespace) -> int:
    settings = load_settings(args.env)
    gateway, github = _clients(settings)
    issues = github.recent_issues(args.repo, since_hours=args.since_hours, limit=args.limit)
    if not issues:
        print(f"No new issues in {args.repo} in the last {args.since_hours} hours.")
        return 0
    triager = Triager(settings, gateway, github, on_event=_progress)
    for issue in issues:
        print(f"\nTriaging {issue.ref} for {args.maintainer}", file=sys.stderr)
        report = triager.run(issue, args.maintainer).to_dict()
        _emit(settings, report, args)
        if report["status"] == "deferred_budget":
            print("Stopping the scan: the budget is spent.", file=sys.stderr)
            break
    return 0


def _emit(settings, report: dict[str, Any], args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(maintainer_summary(report))
    path = write_local(settings, report)
    if path:
        print(f"Draft reply saved to {path} (nothing was posted).", file=sys.stderr)
    if getattr(args, "post", False):
        try:
            url = post_github(settings, report)
            print(f"Posted: {url}", file=sys.stderr)
        except PostRefused as exc:
            print(f"Not posted: {exc}", file=sys.stderr)


def cmd_budget(args: argparse.Namespace) -> int:
    settings = load_settings(args.env)
    ledger = Ledger(settings.home)
    allowance = ledger.allowance(args.maintainer, settings.daily_limit_usd, settings.issue_limit_usd)
    print(f"Local ledger for {args.maintainer}: ${allowance.spent_today:.6f} of ${allowance.daily_limit:.2f} today, ${allowance.remaining:.6f} left.")
    try:
        gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=settings.timeout_s)
        record = gateway.end_user(args.maintainer)
    except (GatewayError, ValueError) as exc:
        print(f"Otari end-user record unavailable: {exc}")
        return 0
    if record is None:
        print("Otari has no end user by that name yet (it is created on the first request).")
    else:
        print("Otari end-user record:")
        print(json.dumps(record, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(args.env)
    print(f"Gateway: {settings.otari_base_url}")
    print(f"API key: {'set' if settings.otari_api_key else 'MISSING'}")
    print(f"Sorter: {settings.sorter.model} (if missing: {settings.sorter.fallback})")
    print(f"Investigator: {settings.investigator.model} (if missing: {settings.investigator.fallback})")
    print(f"Reply screen: {settings.screen.model if settings.screen else 'off'}")
    print(f"Guardrail profile: {settings.injection_profile or 'off'} ({settings.injection_mode})")
    print(f"MCP: {settings.mcp_server_id or settings.mcp_url or 'off'}")
    print(f"Posting: {'ALLOWED for ' + ', '.join(settings.post_repos) if settings.allow_post else 'off (local outbox only)'}")
    if not settings.otari_api_key:
        return 1
    gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=settings.timeout_s)
    try:
        for tool in gateway.tools():
            name = tool.get("id") or tool.get("name") or tool.get("type")
            print(f"  tool {name}: available={tool.get('available')} {tool.get('reason') or ''}".rstrip())
    except GatewayError as exc:
        print(f"Could not list tools: {exc}")
        return 1
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(args.env), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run

    run(args.env)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triagewell", description="Issue triage for maintainers, through Otari.")
    parser.add_argument("--env", default=None, help="path to a .env file (default: ./.env)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("triage", help="triage one issue")
    p.add_argument("ref", nargs="?", help="OWNER/REPO#NUMBER or an issue URL")
    p.add_argument("--file", help="read the issue from a local JSON file instead of GitHub")
    p.add_argument("--as", dest="maintainer", required=True, help="the maintainer this run is billed to")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    p.add_argument("--post", action="store_true", help="post the draft reply to GitHub (also needs TRIAGEWELL_ALLOW_POST)")
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("scan", help="triage the newest open issues of a repo")
    p.add_argument("repo", help="OWNER/REPO")
    p.add_argument("--as", dest="maintainer", required=True)
    p.add_argument("--since-hours", type=int, default=48)
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.add_argument("--post", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("budget", help="show what a maintainer has spent")
    p.add_argument("--as", dest="maintainer", required=True)
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("doctor", help="check the gateway, key and tools")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("serve", help="run the web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="run the MCP server on stdio, for editors")
    p.set_defaults(func=cmd_mcp)

    args = parser.parse_args(argv)
    if args.command == "triage" and not (args.ref or args.file):
        parser.error("triage needs OWNER/REPO#NUMBER or --file")
    try:
        return args.func(args)
    except (ValueError, RuntimeError, GatewayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"error: network: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
