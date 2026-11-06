"""Run Triagewell on every issue in eval/dataset.json through the configured
Otari gateway, with the maintainers' outcome hidden.

    GITHUB_TOKEN=$(gh auth token) uv run python eval/run_eval.py [--limit N] [--workers 3]

Each issue is handed to the triager as if it had just been opened: title, body,
author and creation time only. No labels, no state, no comments. The duplicate
search only sees issues opened before it. Posting is forced off.

Results are appended to eval/runs.jsonl, one line per issue, so an interrupted
run resumes where it stopped. The run stops starting new issues once the spend
recorded in this file passes --max-usd.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from triagewell.config import load_settings
from triagewell.gateway import OtariClient
from triagewell.github import GitHubReader, Issue
from triagewell.triage import Triager

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs.jsonl"
_lock = threading.Lock()


def done_refs() -> dict[str, dict]:
    if not RUNS.exists():
        return {}
    out = {}
    for line in RUNS.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["ref"]] = row
    return out


def spent() -> float:
    return sum(float(r.get("cost_usd") or 0) for r in done_refs().values())


def compact(report: dict, wall_s: float) -> dict:
    inv = report.get("investigation") or {}
    repro = report.get("repro") or {}
    return {
        "ref": f"{report['issue']['repo']}#{report['issue']['number']}",
        "report_id": report["id"],
        "status": report["status"],
        "wall_s": round(wall_s, 1),
        "cost_usd": report.get("cost_usd", 0),
        "kind": (report.get("sort") or {}).get("kind"),
        "labels": inv.get("labels") or [],
        "duplicate_of": inv.get("duplicate_of"),
        "candidates": [d["number"] for d in report.get("duplicates") or []],
        "repro": {k: repro.get(k) for k in ("outcome", "verdict", "pinned", "request_id", "cost_usd", "return_code")} if repro else None,
        "calls": report.get("calls") or [],
        "tool_calls": [{k: t.get(k) for k in ("type", "name", "status", "server", "url")} for t in report.get("tool_calls") or []],
        "evidence": len(inv.get("evidence") or []),
        "unverified": len(inv.get("unverified_evidence") or []),
        "guard": {
            "injection_local": (report.get("guard") or {}).get("injection_local") or [],
            "screen": ((report.get("guard") or {}).get("screen") or {}).get("verdict"),
        },
        "notes": report.get("notes") or [],
    }


def run_one(item: dict, settings, token: str | None) -> dict:
    issue = Issue(
        repo=item["repo"], number=item["number"], title=item["title"], body=item["body"],
        author=item["author"], labels=[], url=item["url"], created_at=item["created_at"], state="open", comments=0,
    )
    gateway = OtariClient(settings.otari_base_url, settings.otari_api_key, timeout=settings.timeout_s)
    github = GitHubReader(token)
    started = time.monotonic()
    try:
        report = Triager(settings, gateway, github).run(issue, "eval").to_dict()
    finally:
        gateway.close()
        github.close()
    return compact(report, time.monotonic() - started)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-usd", type=float, default=6.0)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--refs", nargs="*", default=None, help="only these OWNER/REPO#N")
    args = parser.parse_args()

    settings = load_settings()
    settings = replace(
        settings,
        home=HERE / "home",
        allow_post=False,
        post_repos=[],
        daily_limit_usd=max(settings.daily_limit_usd, args.max_usd + 1),
    )
    token = os.environ.get("GITHUB_TOKEN") or settings.github_token
    dataset = json.loads((HERE / "dataset.json").read_text())
    done = done_refs()
    todo = [i for i in dataset if i["ref"] not in done or (args.retry_errors and done[i["ref"]]["status"] == "error")]
    if args.refs:
        todo = [i for i in todo if i["ref"] in set(args.refs)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} to run, {len(done)} already done, ${spent():.4f} spent so far", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for item in todo:
            if spent() > args.max_usd:
                print(f"stopping: ${spent():.4f} spent, over the ${args.max_usd} cap", flush=True)
                break
            futures[pool.submit(run_one, item, settings, token)] = item
        for future in as_completed(futures):
            item = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # keep going; the row records what broke
                row = {"ref": item["ref"], "status": "error", "cost_usd": 0, "notes": [f"{type(exc).__name__}: {exc}"[:300]]}
            with _lock:
                rows = {**done_refs(), row["ref"]: row}
                RUNS.write_text("".join(json.dumps(r) + "\n" for r in rows.values()))
            repro = (row.get("repro") or {}).get("outcome")
            print(f"{row['ref']}: {row['status']} {row.get('kind')} dup={row.get('duplicate_of')} repro={repro} ${row.get('cost_usd', 0):.4f} {row.get('wall_s')}s", flush=True)
    print(f"total ${spent():.4f}", flush=True)


if __name__ == "__main__":
    main()
