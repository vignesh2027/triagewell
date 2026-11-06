"""Print the README's feature-to-code table with current line numbers.

Each entry names a file and a line of text that marks where the feature is
used. tests/test_readme.py fails when the README's table no longer matches, so
the line numbers cannot drift. Rewrite the README's table with:

    uv run python dev/feature_table.py --write
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FEATURES: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "Model routing and fallbacks",
        "Each role calls a routing policy by name; Otari picks the provider and falls back on failure, and tiers the investigator down when a budget is 80% used. The report records `Otari-Provider`, `Otari-Model` and `Otari-Fallback` for every call.",
        [
            ("src/triagewell/config.py", 'env.get("TRIAGEWELL_INVESTIGATOR_MODEL", "triagewell-investigator")', "role names are policy names"),
            ("src/triagewell/gateway.py", 'fallback=h.get("Otari-Fallback", "false").lower() == "true"', "reads how the call was served"),
            ("src/triagewell/triage.py", "except UnknownModel:", "a missing policy falls back to the catalog model"),
            ("src/triagewell/triage.py", "def _chat_when_free(", "one paced retry when every provider is busy"),
            ("dev/otari-local.yml", "budget_used_pct: {gte: 80}", "the policies, as config"),
        ],
    ),
    (
        "MCP integrations",
        "The investigator asks the repository's DeepWiki over MCP, run by Otari's managed MCP loop (inline server or a workspace-stored server id). Triagewell is also an MCP server itself, so a maintainer can triage from their editor.",
        [
            ("src/triagewell/triage.py", "mcp_servers = [", "inline MCP server on the investigator call"),
            ("src/triagewell/triage.py", "mcp_server_ids = [s.mcp_server_id]", "or the workspace's stored server"),
            ("src/triagewell/mcp_server.py", "def build(env_file: str | None = None) -> MCPServer:", "Triagewell's own MCP server"),
        ],
    ),
    (
        "Web search or web fetch",
        "When an issue links docs, logs or a stack trace, Triagewell reads the first linked page through Otari's fetch tool (`POST /fetch`, no model deciding) and hands it to the investigator fenced as untrusted; the investigator may fetch more with `otari_web_fetch`. Every fetch is recorded with its request id and settled cost, and evidence that cites a page nobody fetched is marked unverified. A workspace that has web access off gets the triage without fetching, not a failed call.",
        [
            ("src/triagewell/gateway.py", "def fetch(self, url: str", "reads a linked page through POST /fetch"),
            ("src/triagewell/triage.py", 'tools.append({"type": "otari_web_fetch"})', "offers the fetch tool to the investigator"),
            ("src/triagewell/triage.py", "def ground_evidence(", "checks claims against real tool calls"),
            ("src/triagewell/gateway.py", "def tool_availability(self)", "leaves the tool out while the workspace has it off"),
        ],
    ),
    (
        "Code execution",
        "The reporter's Python snippet is replayed in Otari's sandbox through `POST /code/execute`, with no model in the loop, against the release the reporter had: the version they state, or the newest PyPI release before the issue was opened. The verdict is strict: reproduced only if the error the report names shows up, refuted only if the report names an error and the snippet runs cleanly, otherwise inconclusive. The settled sandbox charge is read back from Otari's usage API.",
        [
            ("src/triagewell/replay.py", "def choose_pin(", "picks the reporter's release"),
            ("src/triagewell/replay.py", "def sandbox_program(", "installs it and runs the snippet in a fresh interpreter"),
            ("src/triagewell/triage.py", "run = self.gw.execute_code(program", "sends it to the sandbox"),
            ("src/triagewell/triage.py", "def judge_repro(", "the strict verdict"),
            ("src/triagewell/gateway.py", "def settled_cost(", "reads the settled charge back"),
        ],
    ),
    (
        "Budgets and spending limits",
        "A key ceiling caps the team. Every call sends a realistic output cap so Otari's hold stays small. When Otari refuses because the hold does not fit, Triagewell retries once with a cap that does; when the budget is spent, the issue is deferred with Otari's `Retry-After`. A local ledger turns away new issues before they reach the gateway.",
        [
            ("src/triagewell/triage.py", "def _shrink_and_retry(", "retry inside the remaining budget"),
            ("src/triagewell/triage.py", "except BudgetExceeded as exc:", "defer on a spent budget"),
            ("src/triagewell/ledger.py", "def can_start(self) -> bool:", "the local daily limit"),
        ],
    ),
    (
        "Multiple models serving distinct roles",
        "A small open-weight sorter classifies and extracts search terms with reasoning off, a larger open-weight investigator reasons (effort low) with tools, and Llama Guard 4 screens the drafted reply.",
        [
            ("src/triagewell/config.py", "sorter=Role(", "the three roles"),
            ("src/triagewell/config.py", 'effort("TRIAGEWELL_SORTER_REASONING", "none")', "reasoning effort per role"),
            ("src/triagewell/triage.py", "sort_result = self._call(", "sorter call"),
            ("src/triagewell/triage.py", "def _screen(", "reply screen"),
        ],
    ),
    (
        "Guardrails",
        "The sorter call carries Otari's `prompt-injection` guardrail and Triagewell reads its verdict from the `Otari-Guardrails` header. A flagged issue is quarantined: no tools, no sandbox, no reply. Secrets are redacted before any model sees the issue, and the reply is checked again on the way out. On otari.ai the header reads `status: unavailable` until the organization mandates a guardrail, and Triagewell's own checks still quarantine.",
        [
            ("src/triagewell/triage.py", 'guardrails = [{"profile": s.injection_profile, "mode": s.injection_mode}]', "requests the guardrail"),
            ("src/triagewell/gateway.py", "def guardrail_flagged(self) -> bool:", "reads the verdict"),
            ("src/triagewell/guard.py", "def redact_secrets(text: str) -> Redaction:", "secret redaction"),
        ],
    ),
    (
        "Per-user budgets",
        "With a service key, Triagewell names the maintainer on every request, so each maintainer is an Otari end user with a daily budget of their own; a new maintainer can start on a trial budget, and the dashboard shows Otari's own record of their spend. It checks the key first, because otari.ai refuses `user` from an ordinary key.",
        [
            ("src/triagewell/triage.py", "per_user = self.gw.is_service_key()", "names the maintainer only with a service key"),
            ("src/triagewell/gateway.py", 'body["user"] = user', "names the maintainer"),
            ("src/triagewell/gateway.py", 'headers["Otari-End-User-Budget"] = end_user_budget', "picks a new maintainer's budget"),
            ("src/triagewell/service.py", 'status["otari"] = gateway.end_user(maintainer)', "reads Otari's end-user record"),
        ],
    ),
]


def line_of(path: str, anchor: str) -> int:
    lines = (ROOT / path).read_text().splitlines()
    hits = [i + 1 for i, line in enumerate(lines) if anchor in line]
    if not hits:
        raise SystemExit(f"anchor not found in {path}: {anchor!r}")
    return hits[0]


def table() -> str:
    rows = ["| Otari feature | How Triagewell uses it | Where |", "| --- | --- | --- |"]
    for name, how, places in FEATURES:
        links = "<br>".join(f"[{p}:{line_of(p, a)}]({p}#L{line_of(p, a)}) {what}" for p, a, what in places)
        rows.append(f"| {name} | {how} | {links} |")
    return "\n".join(rows)


START, END = "<!-- feature-table -->", "<!-- /feature-table -->"


def write_readme() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{table()}\n{END}{tail}")


if __name__ == "__main__":
    import sys

    if "--write" in sys.argv:
        write_readme()
    else:
        print(table())
