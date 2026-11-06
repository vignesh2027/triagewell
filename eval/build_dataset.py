"""Build the evaluation set: closed public issues with the maintainers' outcome
split off and hidden.

    uv run python eval/build_dataset.py

Needs the `gh` CLI (read-only GraphQL). Writes two files:

* eval/dataset.json: what Triagewell sees (title, body, author, creation time).
  No labels, no state, no comments, no closing information.
* eval/truth.json: what the maintainers did (labels, the issue it was marked a
  duplicate of, whether a merged pull request or commit closed it).

Selection is fixed before any run, in issue-number order inside each stratum,
so nothing is picked after seeing Triagewell's answers. Only issues opened on or
after 2025-01-01 are used: newer than the models' training data, and installable
on the sandbox's Python 3.13.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SINCE = "2025-01-01"

# repo: (duplicates, snippet and fixed, snippet and not fixed, kind-labelled without snippet)
PLAN: dict[str, tuple[int, int, int, int]] = {
    "pydantic/pydantic": (10, 5, 4, 3),
    "pallets/click": (4, 4, 4, 0),
    "mozilla-ai/any-llm": (0, 5, 2, 0),
    "mozilla-ai/any-agent": (2, 3, 2, 0),
    "Textualize/rich": (0, 3, 3, 0),
}

# Maintainer labels that say what kind of issue it is, per repository.
KIND_LABELS = {
    "bug": "bug", "bug v1": "bug", "bug v2": "bug", "type: bug": "bug", "confirmed bug": "bug",
    "feature request": "feature", "enhancement": "feature", "feature": "feature",
    "documentation": "docs", "docs": "docs",
    "question": "question",
}

QUERY = """
query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
 issues(states:CLOSED,first:100,after:$after,orderBy:{field:CREATED_AT,direction:DESC}){
  pageInfo{hasNextPage endCursor}
  nodes{number title body url createdAt closedAt stateReason author{login}
   labels(first:20){nodes{name}}
   closedByPullRequestsReferences(first:5,includeClosedPrs:false){nodes{number merged}}
   timelineItems(itemTypes:[MARKED_AS_DUPLICATE_EVENT,CLOSED_EVENT],first:20){nodes{__typename
    ... on MarkedAsDuplicateEvent{canonical{... on Issue{number}}}
    ... on ClosedEvent{closer{__typename ... on PullRequest{number merged} ... on Commit{oid}}}}}}}}}
"""

_SNIPPET = re.compile(r"```(?:python|py|python3)\s*\n", re.IGNORECASE)


def fetch(repo: str, pages: int = 3) -> list[dict]:
    owner, name = repo.split("/")
    out: list[dict] = []
    after = None
    for _ in range(pages):
        args = ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={QUERY}"]
        if after:
            args += ["-F", f"after={after}"]
        data = json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)
        issues = data["data"]["repository"]["issues"]
        out += issues["nodes"]
        if not issues["pageInfo"]["hasNextPage"] or issues["nodes"][-1]["createdAt"] < SINCE:
            break
        after = issues["pageInfo"]["endCursor"]
    return [i for i in out if i["createdAt"] >= SINCE]


def truth_of(issue: dict) -> dict:
    events = issue["timelineItems"]["nodes"]
    # A duplicate marker also appears on the original's own timeline (pointing at
    # itself) when another issue is marked as its duplicate; only a marker that
    # points elsewhere makes this issue the duplicate.
    canonical = next(
        (
            e["canonical"]["number"]
            for e in events
            if e["__typename"] == "MarkedAsDuplicateEvent" and e.get("canonical") and e["canonical"].get("number") not in (None, issue["number"])
        ),
        None,
    )
    fixed_by = [p["number"] for p in issue["closedByPullRequestsReferences"]["nodes"] if p["merged"]]
    for e in events:
        closer = e.get("closer") if e["__typename"] == "ClosedEvent" else None
        if closer and closer["__typename"] == "PullRequest" and closer.get("merged") and closer["number"] not in fixed_by:
            fixed_by.append(closer["number"])
        if closer and closer["__typename"] == "Commit":
            fixed_by.append(f"commit {closer['oid'][:10]}")
    labels = [l["name"] for l in issue["labels"]["nodes"]]
    kinds = sorted({KIND_LABELS[l.lower()] for l in labels if l.lower() in KIND_LABELS})
    return {
        "labels": labels,
        "kind": kinds[0] if len(kinds) == 1 else None,
        "duplicate": issue["stateReason"] == "DUPLICATE" or canonical is not None or "duplicate" in [l.lower() for l in labels],
        "duplicate_of": canonical,
        "fixed": bool(fixed_by),
        "fixed_by": fixed_by,
        "state_reason": issue["stateReason"],
        "closed_at": issue["closedAt"],
    }


_DUP_REF = re.compile(
    r"(?i)(?:duplicate[sd]?(?:\s+of)?|dup(?:e)?(?:\s+of)?|same\s+as|already\s+(?:reported|tracked)\s+(?:in|at|by)|tracked\s+in|in\s+favou?r\s+of|repurposed|see)"
    r"\s*:?\s*(?:https://github\.com/([\w.-]+/[\w.-]+)/issues/|#)(\d+)"
)


def canonical_from_comments(repo: str, number: int) -> int | None:
    """When a maintainer closed an issue as a duplicate without GitHub's marker,
    the original is usually named in a comment ("Duplicate of #123")."""
    raw = subprocess.run(["gh", "api", f"repos/{repo}/issues/{number}/comments", "--paginate"], check=True, capture_output=True, text=True).stdout
    for comment in json.loads(raw or "[]"):
        if (comment.get("user") or {}).get("type") == "Bot":
            continue
        for match in _DUP_REF.finditer(comment.get("body") or ""):
            other_repo, found = match.group(1), int(match.group(2))
            if (other_repo in (None, repo)) and found != number:
                return found
    return None


def is_bot(issue: dict) -> bool:
    login = (issue.get("author") or {}).get("login") or ""
    return login.endswith("[bot]") or login in {"dependabot", "renovate", "github-actions"}


def main() -> None:
    dataset, truth = [], {}
    for repo, (n_dup, n_fixed, n_unfixed, n_kind) in PLAN.items():
        issues = sorted((i for i in fetch(repo) if not is_bot(i) and (i["body"] or "").strip()), key=lambda i: i["number"])
        chosen: list[dict] = []

        def take(pred, n):
            picked = [i for i in issues if i not in chosen and pred(i)][:n]
            chosen.extend(picked)
            return len(picked)

        got = (
            take(lambda i: truth_of(i)["duplicate"], n_dup),
            take(lambda i: _SNIPPET.search(i["body"]) and truth_of(i)["fixed"], n_fixed),
            take(lambda i: _SNIPPET.search(i["body"]) and not truth_of(i)["fixed"], n_unfixed),
            take(lambda i: truth_of(i)["kind"] is not None and not _SNIPPET.search(i["body"]), n_kind),
        )
        print(f"{repo}: duplicates {got[0]}/{n_dup}, snippet+fixed {got[1]}/{n_fixed}, snippet+not fixed {got[2]}/{n_unfixed}, kind-labelled {got[3]}/{n_kind}")
        for i in chosen:
            ref = f"{repo}#{i['number']}"
            dataset.append({
                "ref": ref, "repo": repo, "number": i["number"], "title": i["title"], "body": i["body"],
                "author": (i.get("author") or {}).get("login") or "unknown", "url": i["url"], "created_at": i["createdAt"],
            })
            truth[ref] = truth_of(i)
            if truth[ref]["duplicate"] and truth[ref]["duplicate_of"] is None:
                found = canonical_from_comments(repo, i["number"])
                truth[ref]["duplicate_of"] = found
                truth[ref]["duplicate_of_source"] = "maintainer comment" if found else "none found"
            elif truth[ref]["duplicate_of"] is not None:
                truth[ref]["duplicate_of_source"] = "GitHub duplicate marker"
    (HERE / "dataset.json").write_text(json.dumps(dataset, indent=1))
    (HERE / "truth.json").write_text(json.dumps(truth, indent=1))
    print(f"{len(dataset)} issues written")


if __name__ == "__main__":
    main()
