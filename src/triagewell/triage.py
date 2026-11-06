"""The triage pipeline: one issue in, one maintainer-ready report out."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import prompts, replay
from .config import Role, Settings
from .gateway import BudgetExceeded, CodeRun, GatewayError, GatewayResult, GuardrailBlocked, OtariClient, UnknownModel
from .github import Candidate, GitHubReader, Issue, keywords
from .guard import fence_untrusted, injection_signals, leaks_in_output, redact_secrets
from .ledger import Ledger

_PY_BLOCK = re.compile(r"```(?:python|py|python3)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_HOLD_REFUSAL = re.compile(r"has \$([0-9]+(?:\.[0-9]+)?) of budget remaining, less than this request's estimated cost of \$([0-9]+(?:\.[0-9]+)?)")
MIN_RETRY_TOKENS = 400
# Longest pause before the one retry after a provider says it is busy.
MAX_BUSY_WAIT_S = 10.0


@dataclass
class Report:
    id: str
    issue: dict[str, Any]
    maintainer: str
    started_at: str
    status: str = "running"  # triaged | quarantined | deferred_budget | error
    sort: dict[str, Any] = field(default_factory=dict)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    repro: dict[str, Any] | None = None
    investigation: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    guard: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_json_object(text: str) -> dict[str, Any]:
    """Pull the last well-formed JSON object out of a model reply."""
    text = _THINK.sub("", text or "").strip()
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = fenced[::-1] if fenced else []
    starts = [m.start() for m in re.finditer(r"\{", text)]
    for start in starts:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for chunk in sorted(candidates, key=len, reverse=True):
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def search_terms(raw: Any, title: str) -> list[str]:
    """The sorter's search terms as a clean list. Models sometimes return one
    comma-separated string; iterating that would search for single letters."""
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]+", raw)
    terms = [str(t).strip().strip("`'\"") for t in raw or [] if isinstance(t, (str, int, float))]
    terms = [t for t in terms if len(t) >= 2]
    return (terms or keywords(title))[:5]


def _from_repl(lines: list[str]) -> list[str]:
    """Turn a pasted interactive session (>>> and ... prompts) back into code."""
    if not any(line.startswith(">>>") for line in lines):
        return lines
    code = []
    for line in lines:
        if line.startswith(">>> ") or line == ">>>":
            code.append(line[4:])
        elif line.startswith("... ") or line == "...":
            code.append(line[4:])
    return code


def extract_python(text: str, max_lines: int = 60) -> str | None:
    for block in _PY_BLOCK.findall(text or ""):
        lines = _from_repl(block.strip("\n").splitlines())
        if 0 < len(lines) <= max_lines:
            return "\n".join(lines)
    return None


def _clean_labels(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    allowed = {label.lower(): label for label in prompts.LABELS}
    out: list[str] = []
    for label in labels:
        key = str(label).strip().lower()
        if key in allowed and allowed[key] not in out:
            out.append(allowed[key])
    return out


_SOURCE_HINTS = {
    "web_fetch": ("fetched", "fetch", "url", "http", "page", "docs", "documentation", "linked"),
    "mcp": ("wiki", "mcp", "deepwiki"),
    "code_execution": ("sandbox", "ran ", "executed", "execution", "run ", "traceback"),
}


def ground_evidence(evidence: Any, tool_calls: list[dict[str, Any]], repro: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Split the model's evidence into items backed by a tool Otari really ran
    and items that only the model claims. Small models like to cite pages they
    never fetched; a maintainer should be able to tell the two apart."""
    if not isinstance(evidence, list):
        return [], []
    used = {str(c.get("type")) for c in tool_calls if c.get("status") == "completed"}
    if repro and repro.get("via", "").startswith("otari"):
        used.add("code_execution")
    backed: list[str] = []
    claimed: list[str] = []
    for item in evidence:
        text = str(item).strip()
        if not text:
            continue
        low = text.lower()
        needs = [kind for kind, hints in _SOURCE_HINTS.items() if any(h in low for h in hints)]
        if "issue text" in low or "issue body" in low or not needs or any(kind in used for kind in needs):
            backed.append(text)
        else:
            claimed.append(text)
    return backed, claimed


_EXC_NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\b")
_GENERIC = {"Error", "Exception", "Warning", "BaseException"}
# A snippet that dies on one of these is usually incomplete, not the bug.
_INCOMPLETE = ("NameError", "SyntaxError", "IndentationError", "ImportError", "ModuleNotFoundError")


def reported_exceptions(report_text: str, snippet: str = "") -> set[str]:
    """Exception and warning names the reporter says they saw, outside their own code."""
    text = report_text.replace(snippet, " ") if snippet else report_text
    return {name for name in _EXC_NAME.findall(text) if name not in _GENERIC}


_FAILURE_WORDS = re.compile(r"(?i)\btraceback\b|\braise[sd]?\b|\bcrash(es|ed)?\b|\bexception\b|\bthrows?\b|\berrors?\b|\bsegfault|\bbl(ow|ows|ew|own) up\b|\bfail(s|ed|ing)? with\b|\bstack ?trace\b")


def describes_failure(report_text: str, snippet: str = "") -> bool:
    """Whether the reporter describes something failing, as opposed to wrong behaviour."""
    text = report_text.replace(snippet, " ") if snippet else report_text
    return bool(_FAILURE_WORDS.search(text))


def judge_repro(stdout: str, stderr: str, return_code: int, report_text: str = "", snippet: str = "") -> tuple[bool | None, str, str]:
    """What a sandbox run of the reporter's snippet tells us.

    Returns (reproduced, verdict, outcome). outcome is one of "reproduced",
    "refuted", "inconclusive" or "not_run". A run only counts as reproducing
    the bug when the error the reporter names shows up, and only refutes it
    when the report names an error and the snippet runs cleanly without it."""
    status, own = replay.split_output(stdout)
    return_code = replay.exit_code(stdout, return_code)
    if (status and status.startswith(replay.INSTALL_FAILED)) or replay.INSTALL_FAILED in stdout:
        spec = status.split(" ", 1)[1] if status and " " in status else "the pinned release"
        return None, f"not run: could not install {spec} in the sandbox", "not_run"
    combined = f"{own}\n{replay.strip_ansi(stderr)}"
    missing = re.search(r"ModuleNotFoundError: No module named '([^']+)'", combined)
    if missing:
        return None, f"not run: the sandbox has no '{missing.group(1)}' package", "not_run"
    if re.search(r"(?i)\bapi[ _-]?key\b.*\b(not|no|missing)\b|\b(missing|no)\b.*\bapi[ _-]?key\b|AuthenticationError", combined):
        return None, "not run: the snippet needs credentials the sandbox does not have", "not_run"
    reported = reported_exceptions(report_text, snippet)
    lines = [line for line in combined.strip().splitlines() if line.strip()]
    raised = [line.strip() for line in lines if re.match(r"^\s*[\w.]*(Error|Exception|Warning|Exit|Interrupt)\b.*", line.strip())]
    last = (raised[-1] if raised else lines[-1] if lines else "non-zero exit")[:200]
    hit = next((name for name in sorted(reported) if name in combined), None)
    if hit:
        line = next((l for l in reversed(lines) if hit in l), last)[:200]
        return True, f"reproduced: {line}", "reproduced"
    failed = return_code != 0 or "Traceback (most recent call last)" in combined
    if failed:
        if last.startswith(_INCOMPLETE):
            return None, f"inconclusive: the snippet looks incomplete ({last})", "inconclusive"
        if reported:
            return None, f"inconclusive: failed with {last}, but the report describes {', '.join(sorted(reported)[:3])}", "inconclusive"
        if not describes_failure(report_text, snippet):
            # The report is about behaviour, not a crash; a crash here is more
            # likely the snippet's own mistake than the reported bug.
            return None, f"inconclusive: failed with {last}, but the report does not describe an error", "inconclusive"
        return True, f"reproduced: {last}", "reproduced"
    if reported:
        return False, f"refuted: ran cleanly, without the {', '.join(sorted(reported)[:3])} the report describes", "refuted"
    return None, "inconclusive: ran without an exception; compare the output with the report", "inconclusive"


class Triager:
    def __init__(
        self,
        settings: Settings,
        gateway: OtariClient,
        github: GitHubReader | None = None,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.s = settings
        self.gw = gateway
        self.gh = github
        self.ledger = Ledger(settings.home)
        self.on_event = on_event or (lambda kind, data: None)
        self.sleep: Callable[[float], None] = time.sleep

    # -- gateway helpers -------------------------------------------------

    def _call(self, report: Report, role: Role, messages: list[dict[str, Any]], **kwargs: Any) -> GatewayResult:
        """Call one role. If the role's routing policy does not exist on this
        workspace yet, retry once on the role's catalog model and say so."""
        # Only a service key may name the maintainer as an Otari end user; an
        # ordinary key that sends `user` is refused, so it goes in metadata instead.
        per_user = self.gw.is_service_key()
        common = dict(
            user=report.maintainer if per_user else None,
            end_user_budget=self._budget_id() if per_user else None,
            max_tokens=role.max_tokens,
            reasoning_effort=role.reasoning_effort,
            metadata={"app": "triagewell", "role": role.name, "repo": report.issue["repo"], "maintainer": report.maintainer},
            session_label=f"triagewell {report.issue['repo']}#{report.issue['number']}",
        )
        common.update(kwargs)
        self.on_event("call", {"role": role.name, "model": role.model})
        model = role.model
        try:
            result = self._chat_when_free(report, role, model, messages, common)
        except UnknownModel:
            if not role.fallback:
                raise
            report.notes.append(
                f"Routing policy '{role.model}' is not defined on this workspace; used catalog model '{role.fallback}' directly."
            )
            model = role.fallback
            result = self._chat_within_budget(report, role, model, messages, common)
        except BudgetExceeded as exc:
            result = self._shrink_and_retry(report, role, model, messages, common, exc)
        entry = {"role": role.name, **result.summary()}
        report.calls.append(entry)
        report.cost_usd += result.cost_usd
        self.on_event("called", entry)
        return result

    def _chat_within_budget(self, report: Report, role: Role, model: str, messages: list[dict[str, Any]], common: dict[str, Any]) -> GatewayResult:
        try:
            return self._chat_when_free(report, role, model, messages, common)
        except BudgetExceeded as exc:
            return self._shrink_and_retry(report, role, model, messages, common, exc)

    def _chat_when_free(self, report: Report, role: Role, model: str, messages: list[dict[str, Any]], common: dict[str, Any]) -> GatewayResult:
        """Otari already falls back across providers; when every one of them is
        rate limited it answers 429. One paced retry usually gets through."""
        try:
            return self.gw.chat(model, messages, **common)
        except GatewayError as exc:
            if exc.status != 429 or isinstance(exc, BudgetExceeded):
                raise
            hinted = exc.headers.get("Retry-After")
            wait = min(float(hinted), MAX_BUSY_WAIT_S) if hinted and hinted.isdigit() else 3.0
            report.notes.append(f"{role.name}: Otari said the model was busy ({exc.code}); retried once after {wait:.0f} s.")
            self.sleep(wait)
            return self.gw.chat(model, messages, **common)

    def _shrink_and_retry(
        self, report: Report, role: Role, model: str, messages: list[dict[str, Any]], common: dict[str, Any], exc: BudgetExceeded
    ) -> GatewayResult:
        """Otari holds a request's worst case (its whole output cap) before sending it.
        When the refusal says the hold, not the spend, is what does not fit, one retry
        with a smaller output cap can still finish the issue inside the budget."""
        match = _HOLD_REFUSAL.search(exc.detail)
        if not match:
            raise exc
        remaining, estimate = float(match.group(1)), float(match.group(2))
        cap = int(common["max_tokens"] * (remaining / estimate) * 0.8) if estimate > 0 else 0
        if cap < MIN_RETRY_TOKENS:
            raise exc
        report.notes.append(
            f"{role.name}: Otari said ${remaining:.6f} was left against an estimated ${estimate:.6f}; retried with max_tokens {cap} instead of {common['max_tokens']}."
        )
        return self.gw.chat(model, messages, **{**common, "max_tokens": cap})

    def _budget_id(self) -> str | None:
        """The end-user budget to start a new maintainer on, as Otari's id."""
        if not hasattr(self, "_resolved_budget"):
            self._resolved_budget = self.gw.resolve_end_user_budget(self.s.end_user_budget)
        return self._resolved_budget

    def _pin(self, issue: Issue, body: str) -> replay.Pin | None:
        """The release the reporter most likely had, from PyPI, or None."""
        if self.gh is None:
            return None
        name = self.gh.package_name(issue.repo)
        if not name:
            return None
        return replay.choose_pin(name, f"{issue.title}\n{body}", issue.created_at, replay.releases(name))

    # -- pipeline --------------------------------------------------------

    def run(self, issue: Issue, maintainer: str) -> Report:
        report = Report(
            id=uuid.uuid4().hex[:12],
            issue=issue.to_dict(),
            maintainer=maintainer,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        allowance = self.ledger.allowance(maintainer, self.s.daily_limit_usd, self.s.issue_limit_usd)
        report.budget = {
            "local_daily_limit_usd": allowance.daily_limit,
            "local_spent_today_usd": round(allowance.spent_today, 6),
            "issue_limit_usd": allowance.issue_limit,
        }
        if not allowance.can_start:
            report.status = "deferred_budget"
            report.notes.append(
                f"{maintainer} has ${allowance.remaining:.4f} left today, less than one issue may cost "
                f"(${allowance.issue_limit:.2f}). Skipped before calling the gateway."
            )
            return self._finish(report)

        try:
            self._run(report, issue)
        except BudgetExceeded as exc:
            report.status = "deferred_budget"
            report.budget["otari_refusal"] = {"scope": exc.scope, "retry_after_s": exc.retry_after, "detail": exc.detail[:300]}
            when = f"retry in {exc.retry_after} s" if exc.retry_after else "no retry time given"
            report.notes.append(f"Otari refused the call ({exc.scope} budget): {exc.detail[:200]} ({when}).")
        except GatewayError as exc:
            report.status = "error"
            report.notes.append(f"Gateway error {exc.status} {exc.code or ''}: {exc.detail[:300]}")
        return self._finish(report)

    def _run(self, report: Report, issue: Issue) -> None:
        s = self.s
        raw = f"{issue.title}\n\n{issue.body}"
        redaction = redact_secrets(raw)
        clean_body = redact_secrets(issue.body).text
        local_hits = injection_signals(raw)
        report.guard = {
            "secrets_redacted": redaction.found,
            "injection_local": local_hits,
            "injection_gateway": [],
            "screen": None,
            "reply_leaks": [],
        }
        if redaction.found:
            report.notes.append(f"Redacted {len(redaction.found)} secret(s) from the issue before any model saw it.")
        if not self.gw.is_service_key():
            report.budget["otari_per_user"] = False
            report.notes.append(
                "This Otari key is not a service key, so Otari keeps no budget per maintainer; "
                "only the key's own budget and Triagewell's local ledger apply."
            )
        issue_block = fence_untrusted("ISSUE", redaction.text)

        # 1. Sorter: small, fast model. Otari's prompt-injection guardrail runs on this call.
        guardrails = [{"profile": s.injection_profile, "mode": s.injection_mode}] if s.injection_profile else None
        quarantined = bool(local_hits)
        try:
            sort_result = self._call(
                report,
                s.sorter,
                [
                    {"role": "system", "content": prompts.SORTER_SYSTEM},
                    {"role": "user", "content": prompts.sorter_user(issue_block, issue.repo, issue.title)},
                ],
                guardrails=guardrails,
            )
            report.guard["injection_gateway"] = sort_result.guardrails
            if sort_result.guardrail_flagged:
                quarantined = True
            sort = parse_json_object(sort_result.content)
        except GuardrailBlocked as exc:
            quarantined = True
            report.guard["injection_gateway"] = [{"profile": s.injection_profile, "mode": "block", "valid": False, "status": "evaluated"}]
            report.notes.append(f"Otari's guardrail refused the issue text ({exc.code}).")
            sort = {}
        sort.setdefault("summary", issue.title)
        sort["search_terms"] = search_terms(sort.get("search_terms"), issue.title)
        links = [u for u in (sort.get("links") or []) if isinstance(u, str) and u.startswith("http")]
        links += [u for u in _URL.findall(clean_body) if u not in links]
        sort["links"] = links[:3]
        report.sort = sort
        self.on_event("sorted", sort)

        # 2. Duplicate search, read-only against GitHub.
        candidates: list[Candidate] = []
        if self.gh is not None:
            candidates = self.gh.similar(issue, sort["search_terms"])
        report.duplicates = [c.__dict__ for c in candidates]

        # 3. Run the reporter's snippet in Otari's sandbox, with no model involved.
        snippet = extract_python(clean_body)
        run: CodeRun | None = None
        pin = None
        sandbox_failure = "not made"
        if snippet and s.code_execution and not quarantined:
            pin = self._pin(issue, clean_body) if s.pin_release else None
            program = replay.sandbox_program(snippet, pin)
            self.on_event("replay", {"pin": replay.pin_dict(pin)})
            try:
                run = self.gw.execute_code(program, user=report.maintainer if self.gw.is_service_key() else None, timeout_seconds=replay.DEADLINE_S + 10)
            except GatewayError as exc:
                if isinstance(exc, BudgetExceeded):
                    raise
                # A sandbox failure costs the maintainer the verdict, not the whole triage.
                why = {403: "refused by the firewall in front of the gateway", 504: "timed out at the gateway edge"}.get(exc.status, f"failed at the gateway ({exc.status})")
                report.notes.append(f"The sandbox run was {why}; no verdict.")
                sandbox_failure = why
                run = None
        if snippet and s.code_execution and not quarantined and run is None:
            report.repro = {"via": "otari /code/execute", "pinned": replay.pin_dict(pin), "code": snippet, "reproduced": None, "outcome": "not_run", "verdict": f"not run: the sandbox call was {sandbox_failure}"}
        elif snippet and s.code_execution and not quarantined:
            if not run.cost_usd:
                # The sandbox response carries no cost; Otari's settled record does.
                settled = self.gw.settled_cost(run.request_id)
                if settled is not None:
                    run.cost_usd = settled
            reproduced, verdict, outcome = judge_repro(run.stdout, run.stderr, run.return_code, clean_body, snippet)
            _, own_stdout = replay.split_output(run.stdout)
            report.repro = {
                "via": "otari /code/execute",
                "request_id": run.request_id,
                "cost_usd": run.cost_usd,
                "pinned": replay.pin_dict(pin),
                "added_packages": replay.extra_installs(run.stdout),
                "added_imports": replay.added_imports(run.stdout),
                "code": snippet,
                "stdout": own_stdout[-2000:],
                "stderr": replay.strip_ansi(run.stderr)[-2000:],
                "return_code": replay.exit_code(run.stdout, run.return_code),
                "reproduced": reproduced,
                "outcome": outcome,
                "verdict": verdict,
            }
            report.cost_usd += run.cost_usd
            self.on_event("repro", report.repro)
        elif snippet and quarantined:
            report.repro = {"via": "skipped", "verdict": "not run: the issue was quarantined", "reproduced": None, "outcome": "not_run"}

        # 4. Read the pages the issue links through Otari's fetch tool, with no
        # model choosing whether to: what was fetched is then a recorded fact.
        fetched: list[dict[str, Any]] = []
        if not quarantined and s.web_fetch and sort["links"] and self.gw.tool_availability().get("otari_web_fetch") is not False:
            # Each fetch is billed like a sandbox run, so only the first few distinct pages.
            pages = list(dict.fromkeys(u.split("#", 1)[0].rstrip(".,;:)]") for u in sort["links"]))
            for url in pages[: s.max_fetches]:
                try:
                    page = self.gw.fetch(url)
                except GatewayError as exc:
                    report.notes.append(f"Could not fetch {url} through Otari ({exc.status}).")
                    continue
                settled = self.gw.settled_cost(page["request_id"])
                page["cost_usd"] = settled or 0.0
                report.cost_usd += page["cost_usd"]
                fetched.append(page)
                self.on_event("fetched", {"url": url, "request_id": page["request_id"]})
        pages_text = "\n\n".join(fence_untrusted("PAGE", f"{p['final_url']}\n{p['text'][:3000]}") for p in fetched)

        # 5. Investigator: larger model with Otari's tools, unless quarantined.
        tools: list[dict[str, Any]] = []
        mcp_servers = None
        mcp_server_ids = None
        notes = []
        if not quarantined:
            if s.web_fetch and sort["links"]:
                tools.append({"type": "otari_web_fetch"})
                notes.append("otari_web_fetch for the linked pages")
            if s.code_execution:
                tools.append({"type": "otari_code_execution"})
                notes.append("otari_code_execution for a short Python check")
            if s.mcp_server_id:
                mcp_server_ids = [s.mcp_server_id]
                notes.append("the repository wiki over MCP")
            elif s.mcp_url:
                mcp_servers = [
                    {
                        "name": "repo_wiki",
                        "url": s.mcp_url,
                        "purpose_hint": f"Ask how {issue.repo} works. Pass repoName='{issue.repo}'.",
                        "allowed_tools": s.mcp_allowed_tools or None,
                    }
                ]
                notes.append(f"the repository wiki over MCP (repoName '{issue.repo}')")
        available = self.gw.tool_availability()
        dropped = [t["type"] for t in tools if available.get(t["type"]) is False]
        if dropped:
            tools = [t for t in tools if t["type"] not in dropped]
            notes = [n for n in notes if n.split(" ", 1)[0] not in dropped]
            report.notes.append(f"Skipped {', '.join(dropped)}: Otari says this workspace has not turned it on.")
        dup_text = "\n".join(f"#{c.number} [{c.state}] {c.title}" for c in candidates) or "none found"
        repro_text = json.dumps({k: v for k, v in (report.repro or {}).items() if k != "code"}) if report.repro else "no snippet to run"
        investigator_messages = [
            {"role": "system", "content": prompts.INVESTIGATOR_SYSTEM},
            {
                "role": "user",
                "content": prompts.investigator_user(
                    repo=issue.repo,
                    number=issue.number,
                    title=issue.title,
                    issue_block=issue_block,
                    sort_json=json.dumps(sort, indent=1),
                    duplicates=dup_text,
                    repro=repro_text,
                    quarantined=quarantined,
                    tools_note=", ".join(notes) if notes else "none",
                    pages=pages_text,
                ),
            },
        ]
        try:
            result = self._call(
                report,
                s.investigator,
                investigator_messages,
                tools=tools or None,
                mcp_servers=mcp_servers,
                mcp_server_ids=mcp_server_ids,
                max_tool_iterations=s.max_tool_iterations if (tools or mcp_servers or mcp_server_ids) else None,
                temperature=0.2,
            )
        except GatewayError as exc:
            # The edge in front of the hosted gateway gives up on long requests,
            # and the tool loop (wiki questions, sandbox runs) is the slow part.
            # The reporter's snippet has already run, so answer once without tools.
            if exc.status not in {502, 503, 504} or not (tools or mcp_servers or mcp_server_ids):
                raise
            report.notes.append(f"investigator: the gateway timed out ({exc.status}) during the tool loop; retried once without tools.")
            result = self._call(report, s.investigator, investigator_messages, temperature=0.2)
        direct = [
            {"type": "web_fetch", "status": "completed", "url": p["final_url"], "via": "POST /fetch", "request_id": p["request_id"], "cost_usd": p["cost_usd"]}
            for p in fetched
        ]
        report.tool_calls = direct + [_trim_tool_call(c) for c in result.tool_calls]
        found = parse_json_object(result.content)
        if not found:
            found = {"summary": _THINK.sub("", result.content).strip()[:800], "confidence": "low"}
            report.notes.append("The investigator did not return JSON; its text is shown as the summary.")
        found["labels"] = _clean_labels(found.get("labels"))
        if quarantined:
            found["labels"] = [label for label in found["labels"] if label in {"spam", "needs-info"}] + ["security-review"]
            found["reply_draft"] = ""
            # Never let a quarantined issue's own text choose the next action.
            found["next_step"] = (
                "Read this issue yourself before acting on it. It contains instructions aimed at automated "
                "tools (see Safety); do not follow its links or label changes."
            )
        dup = found.get("duplicate_of")
        if dup is not None and not any(c.number == _as_int(dup) for c in candidates):
            report.notes.append(f"Ignored duplicate_of #{dup}: it was not among the issues the search found.")
            found["duplicate_of"] = None
        backed, claimed = ground_evidence(found.get("evidence"), direct + list(result.tool_calls), report.repro)
        found["evidence"] = backed
        if claimed:
            found["unverified_evidence"] = claimed
            report.notes.append(f"{len(claimed)} evidence item(s) cite a source no tool call backs; listed as unverified.")
        report.investigation = found

        # 6. Outbound checks on the drafted reply.
        reply = str(found.get("reply_draft") or "")
        if reply:
            leaks = leaks_in_output(reply)
            if leaks:
                found["reply_draft"] = redact_secrets(reply).text
                report.guard["reply_leaks"] = leaks
                report.notes.append("The drafted reply contained secret-shaped text; it was redacted and posting is blocked.")
            if s.screen is not None:
                report.guard["screen"] = self._screen(report, found.get("summary", ""), found["reply_draft"])

        report.status = "quarantined" if quarantined else "triaged"

    def _screen(self, report: Report, summary: str, reply: str) -> dict[str, Any]:
        """Third model, third role: a safety classifier reads the drafted reply."""
        assert self.s.screen is not None
        try:
            result = self._call(
                report,
                self.s.screen,
                [
                    {"role": "user", "content": f"Summarize the status of this GitHub issue: {summary[:500]}"},
                    {"role": "assistant", "content": reply},
                ],
                temperature=None,
            )
        except GatewayError as exc:
            return {"verdict": "skipped", "reason": f"{exc.status} {exc.code or ''}".strip()}
        text = _THINK.sub("", result.content).strip().lower()
        verdict = "unsafe" if text.startswith("unsafe") else "safe" if text.startswith("safe") else "unclear"
        return {"verdict": verdict, "raw": text[:60], "model": result.model_served or result.model_requested}

    def _finish(self, report: Report) -> Report:
        report.notes = list(dict.fromkeys(report.notes))
        report.cost_usd = round(report.cost_usd, 6)
        self.ledger.record(report.maintainer, f"{report.issue['repo']}#{report.issue['number']}", report.cost_usd, report.status, report.id)
        save_report(self.s.home, report)
        self.on_event("done", {"status": report.status, "cost_usd": report.cost_usd})
        return report


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).lstrip("#"))
    except (TypeError, ValueError):
        return None


def _trim_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    keep = {k: call.get(k) for k in ("id", "type", "status", "url", "query", "server", "name", "code", "stdout", "stderr", "exit_code", "output", "error")}
    for key in ("stdout", "stderr", "output"):
        if isinstance(keep.get(key), str):
            keep[key] = keep[key][:1500]
    return {k: v for k, v in keep.items() if v not in (None, "")}


def save_report(home: Path, report: Report) -> Path:
    path = Path(home) / "reports" / f"{report.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path


def load_reports(home: Path, limit: int = 50) -> list[dict[str, Any]]:
    folder = Path(home) / "reports"
    if not folder.exists():
        return []
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for path in files:
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return out


def load_report(home: Path, report_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-f]{12}", report_id):
        return None
    path = Path(home) / "reports" / f"{report_id}.json"
    return json.loads(path.read_text()) if path.exists() else None
