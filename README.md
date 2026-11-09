<p align="center"><img src="assets/logo-512.png" width="120" alt="Triagewell logo"></p>

<h1 align="center">Triagewell</h1>

<p align="center">Issue triage for open-source maintainers, run through the <a href="https://otari.ai">Otari</a> gateway on open-weight models.</p>

Before a maintainer reads a new bug report, Triagewell has already replayed the reporter's code. It installs the
release the reporter was on (the version they state, or else the newest PyPI release published before they opened the
issue) in Otari's sandbox, runs their snippet in a fresh interpreter, and says **reproduced**, **refuted** or
**inconclusive**, with the output. The verdict is strict on purpose: a crash only counts if it is the error the report
describes, and a clean run only refutes a report that names an error.

The second rule is about the report itself: Triagewell does not assert what it did not check. Every piece of evidence
the model cites is matched against the tool calls Otari actually ran (`otari_tool_calls`, the sandbox run); anything
that cites a page, wiki or run that never happened is shown separately as **unverified**.

Around those two, it does the usual triage: kind, labels, likely duplicates (only among issues opened earlier), a
next step and a draft reply, in a few seconds and about a cent. Nothing is posted anywhere unless the maintainer turns
posting on.

<!-- eval-summary -->
**Measured, not claimed.** I replayed 54 closed public issues from pydantic/pydantic, pallets/click, mozilla-ai/any-llm, mozilla-ai/any-agent, Textualize/rich through hosted
Otari as if they had just been opened (no labels, no comments, no state) and scored Triagewell against what the
maintainers actually did. Details, caveats and every issue are in [Evaluation](#evaluation) and on the dashboard's
Evaluation page.

| What | Result |
| --- | --- |
| When the sandbox said **reproduced**, a fix was merged | 2/2 = 100% (95% CI 34% to 100%); first run 2/3 = 67% (95% CI 21% to 94%) |
| When it said **refuted**, no fix was merged | 2/3 = 67% (95% CI 21% to 94%); first run 2/3 = 67% (95% CI 21% to 94%) |
| Snippet runs with a decisive verdict | 5/28 = 18% (95% CI 8% to 36%); first run 6/32 = 19% (95% CI 9% to 35%) |
| `bug` label precision | 12/14 = 86% (95% CI 60% to 96%); first run 12/13 = 92% (95% CI 67% to 99%) |
| Duplicate flagged, and it was one | 10/12 = 83% (95% CI 55% to 95%); first run 4/8 = 50% (95% CI 22% to 78%) |
| Evidence the model cited without a tool call behind it (shown as unverified) | 4/103 = 4% (95% CI 2% to 10%); first run 6/96 = 6% (95% CI 3% to 13%) |
| Cost | $0.38 for all 54, median $0.0102 per issue, reconciled with Otari's settled records |
<!-- /eval-summary -->

Issue trackers are public inboxes, so Triagewell treats every issue as untrusted input. Secrets pasted into an issue are
removed before any model sees them. An issue that tries to give instructions to an automated reader is quarantined:
no tools, no sandbox, no reply, just a note for a human. Every maintainer has a daily budget of their own, so a flood of
spam issues cannot burn the team's money.

![A triage report in the Triagewell dashboard](assets/screenshot-report.png)

Built for Mozilla's Developers Wilding hackathon. Every model call goes through Otari, and every Otari feature below is
used for a reason the feature exists, not to tick a box.

## Prior art

Issue-triage assistants are not new, and running a reporter's code is not new either, so here is what I checked on
6 Nov 2026 and where Triagewell sits.

| Tool | Runs the reporter's code? | When | Verdict to the maintainer? | Checks cited evidence against tool calls? |
| --- | --- | --- | --- | --- |
| [Dosu](https://dosu.dev) | not that I could confirm | triage: labels, duplicates, draft answers | no | not documented |
| [GitHub AI issue triage](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai), [AI labeler action](https://github.blog/changelog/2025-09-05-github-actions-ai-labeler-and-moderator-with-the-github-models-inference-api) | no | triage | no | no |
| [Linear Triage Intelligence](https://linear.app/docs/triage-intelligence), [Mozilla bugbug](https://github.com/mozilla/bugbug) | no | triage | no | no |
| [Copilot coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent), [OpenHands resolver](https://docs.openhands.dev/openhands/usage/run-openhands/github-action), [SWE-agent](https://github.com/SWE-agent/SWE-agent), Devin, Jules | yes, inside a fixing run (SWE-agent's default prompt writes a reproduction script first) | when asked to fix | not as a triage verdict | no |
| [TypeScript Twoslash Repro Action](https://github.com/microsoft/TypeScript-Twoslash-Repro-Action), [rust-lang glacier](https://forge.rust-lang.org/triagebot/glacier.html) | yes, code blocks from issues, no LLM | on labelled issues, nightly | yes, per compiler version | n/a |
| [Metabase Repro-Bot](https://www.metabase.com/blog/reprobot-github-issue-triage-agent) (Apr 2026) | yes, an LLM agent in a sandbox | triage, but a person starts each run on purpose | yes, in Linear | no |
| [githubclaw bug_reproducer](https://docs.rs/crate/githubclaw/latest/source/defaults/agents/bug_reproducer.md) | yes, Claude with a shell, Docker preferred | triage | yes, `reproduced=true/false` comment | no |

Research has the same idea for fixing, not triage: LIBRO (ICSE 2023) wrote bug-reproducing tests for 33.5% of
Defects4J bugs, Issue2Test (2025) about a third of SWT-bench-lite, Google's agent-written reproduction tests (2025)
helped its repair system fix about 30% more bugs.

So Triagewell is not the first tool to run an issue's code. What I did not find anywhere is the combination: the
reporter's snippet replayed automatically on every new issue, in a disposable sandbox, **against the release the
reporter had**, with a strict three-way verdict, every cited source checked against the calls the gateway actually
ran, and the whole thing measured against what maintainers later did (below). If you know a tool that does this, open
an issue and I will add it here.

## Evaluation

**What was run.** 54 closed public issues opened in 2025 or later (newer than the models' training data), from
pydantic/pydantic (22), pallets/click (12), mozilla-ai/any-llm (7), mozilla-ai/any-agent (7) and Textualize/rich (6).
The set was fixed before any run by [eval/build_dataset.py](eval/build_dataset.py): within each repository, in issue
number order, every issue closed as a duplicate, then issues with a Python snippet that a merged pull request or
commit closed, then issues with a snippet that nothing fixed, then a few kind-labelled issues without a snippet.
Bots and empty issues are left out.

**What Triagewell saw.** Title, body, author and creation time, nothing else
([eval/run_eval.py](eval/run_eval.py)). No labels, no comments, no state. The duplicate search only sees issues opened
before the one being triaged. Posting was forced off.

**What it was scored against** ([eval/score.py](eval/score.py), ground truth in [eval/truth.json](eval/truth.json)):
the kind label the maintainers applied (20 issues have one), whether they closed it as a duplicate and of which
issue (16 duplicates; the original is known for 8, from GitHub's duplicate marker or a maintainer comment), and
whether a merged pull request or commit closed it (21 issues). Every proportion has a 95% Wilson interval, because
these samples are small.

<!-- eval-results -->
| Measure | Round 2 | Round 1 |
| --- | --- | --- |
| Kind matches the maintainers' label | 14/20 = 70% (95% CI 48% to 86%) | 16/20 = 80% (95% CI 58% to 92%) |
| `bug` label precision | 12/14 = 86% (95% CI 60% to 96%) | 12/13 = 92% (95% CI 67% to 99%) |
| `bug` label recall | 12/16 = 75% (95% CI 50% to 90%) | 12/16 = 75% (95% CI 50% to 90%) |
| Duplicate flag precision | 10/12 = 83% (95% CI 55% to 95%) | 4/8 = 50% (95% CI 22% to 78%) |
| Duplicate flag recall | 10/16 = 62% (95% CI 39% to 82%) | 4/16 = 25% (95% CI 10% to 50%) |
| Named the same original as the maintainer | 3/8 = 38% (95% CI 14% to 69%) | 0/8 = 0% (95% CI 0% to 32%) |
| Search surfaced that original | 3/8 = 38% (95% CI 14% to 69%) | 0/8 = 0% (95% CI 0% to 32%) |
| Non-duplicates wrongly flagged | 2/38 = 5% (95% CI 2% to 17%) | 4/38 = 10% (95% CI 4% to 24%) |
| Snippet runs with a decisive verdict | 5/28 = 18% (95% CI 8% to 36%) | 6/32 = 19% (95% CI 9% to 35%) |
| "Reproduced" and a fix was merged | 2/2 = 100% (95% CI 34% to 100%) | 2/3 = 67% (95% CI 21% to 94%) |
| Fixed bugs that reproduced | 2/14 = 14% (95% CI 4% to 40%) | 2/17 = 12% (95% CI 3% to 34%) |
| "Refuted" and no fix was merged | 2/3 = 67% (95% CI 21% to 94%) | 2/3 = 67% (95% CI 21% to 94%) |
| Base rate: fixed, among snippets that ran | 14/28 = 50% (95% CI 33% to 67%) | 17/32 = 53% (95% CI 36% to 69%) |
| Evidence items marked unverified | 4/103 = 4% (95% CI 2% to 10%) | 6/96 = 6% (95% CI 3% to 13%) |
| Real issues wrongly quarantined | 0/54 = 0% (95% CI 0% to 7%) | 0/54 = 0% (95% CI 0% to 7%) |

Sandbox outcomes, round 2: {'inconclusive': 23, 'not_run': 13, 'refuted': 3, 'reproduced': 2}; release pinned by: {'newest release before the issue': 21, 'stated in the issue': 20}. Round 1: {'inconclusive': 26, 'not_run': 9, 'refuted': 3, 'reproduced': 3}.

Cost and latency, round 2: $0.3762 for 54 issues (32 sandbox runs at $0.01 each), median 24.3 s per issue, p90 42.8 s. Otari's median response time by role: sorter 516 ms, investigator 10563 ms, screen 328 ms. Otari's settled records for the same 194 request ids add up to $0.3762 (0 missing).
162 model calls, 0 served by a fallback provider, 20 DeepWiki MCP calls completed, 2 investigator tool loops retried without tools after the edge timed out.

| Otari feature | Issue | Request id |
| --- | --- | --- |
| Sorter, small model | pydantic/pydantic#12546 | `93de94f8-4e68-4c5a-acd7-83bc90300a63` |
| Reply screen, Llama Guard | pydantic/pydantic#12546 | `136eefbe-328d-4a0f-b088-4d92eef9e521` |
| Managed MCP (DeepWiki) | pydantic/pydantic#12544 | `6f0760b4-611d-4097-a614-04cf8a077d64` |
| Code execution: reproduced | pydantic/pydantic#12503 | `55fb3df4-3e18-44cf-a203-a29cc9428b8b` |
| Code execution: refuted | pydantic/pydantic#12564 | `736467ed-a005-4a0b-8824-7103bc1f8604` |
| Routing fallback | none in this run |  |
<!-- /eval-results -->

**Two rounds, and why.** Round 1 exposed five defects: the sorter sometimes returned its search terms as one string,
which the code then split into single letters; GitHub's search ANDs every word, so five terms usually found nothing;
many snippets used names they never imported; some sandbox runs outlasted the edge in front of otari.ai; and a crash
counted as a reproduction even when the report described wrong output, not an error. Round 2 addresses all five and is
measured on the same 54 issues, so its numbers are optimistic. The fixes moved duplicate detection a lot and the
sandbox very little. Both rounds' raw rows are in
[eval/runs-v1.jsonl](eval/runs-v1.jsonl) and [eval/runs.jsonl](eval/runs.jsonl).

**How to read it.**

* A merged fix is a stand-in for "the bug was real", and an imperfect one: a real bug closed as a duplicate, or fixed
  upstream, counts as "not fixed". The reproduction rows compare the verdict with that stand-in, next to its base rate.
* Most snippets do not give a decisive verdict. Some describe wrong output rather than an error, which no mechanical
  check can judge; some need API keys or services the sandbox does not have. Triagewell says so instead of guessing.
* Of the 41 issues with a snippet, 7 sandbox calls were refused with an HTML 403 by the AWS load balancer in front of
  otari.ai, not by Otari (the same snippet alone runs; wrapped in the install-and-run program it is refused), and 2
  timed out at the edge. Triagewell reports these as "not run" and does not try to slip past the firewall; I reported it to
  Otari as [mozilla-ai/otari#3012](https://github.com/mozilla-ai/otari/issues/3012).
* The investigator asks DeepWiki, which describes each repository as it is today, so it can know about later fixes.
  The sandbox verdict cannot: it runs the release from before the issue.
* The reply screen and the kind are model output; the reproduction verdict and the unverified marks are not.

Re-run it: `uv run python eval/build_dataset.py`, then
`GITHUB_TOKEN=$(gh auth token) uv run python eval/run_eval.py`, then `uv run python eval/score.py --settle --readme`.

## How it uses Otari

<!-- feature-table -->
| Otari feature | How Triagewell uses it | Where |
| --- | --- | --- |
| Model routing and fallbacks | Each role calls a routing policy by name; Otari picks the provider and falls back on failure, and tiers the investigator down when a budget is 80% used. The report records `Otari-Provider`, `Otari-Model` and `Otari-Fallback` for every call. | [src/triagewell/config.py:102](src/triagewell/config.py#L102) role names are policy names<br>[src/triagewell/gateway.py:224](src/triagewell/gateway.py#L224) reads how the call was served<br>[src/triagewell/triage.py:265](src/triagewell/triage.py#L265) a missing policy falls back to the catalog model<br>[src/triagewell/triage.py:287](src/triagewell/triage.py#L287) one paced retry when every provider is busy<br>[dev/otari-local.yml:43](dev/otari-local.yml#L43) the policies, as config |
| MCP integrations | The investigator asks the repository's DeepWiki over MCP, run by Otari's managed MCP loop (inline server or a workspace-stored server id). Triagewell is also an MCP server itself, so a maintainer can triage from their editor. | [src/triagewell/triage.py:520](src/triagewell/triage.py#L520) inline MCP server on the investigator call<br>[src/triagewell/triage.py:517](src/triagewell/triage.py#L517) or the workspace's stored server<br>[src/triagewell/mcp_server.py:22](src/triagewell/mcp_server.py#L22) Triagewell's own MCP server |
| Web search or web fetch | When an issue links docs, logs or a stack trace, Triagewell reads the first linked page through Otari's fetch tool (`POST /fetch`, no model deciding) and hands it to the investigator fenced as untrusted; the investigator may fetch more with `otari_web_fetch`. Every fetch is recorded with its request id and settled cost, and evidence that cites a page nobody fetched is marked unverified. A workspace that has web access off gets the triage without fetching, not a failed call. | [src/triagewell/gateway.py:251](src/triagewell/gateway.py#L251) reads a linked page through POST /fetch<br>[src/triagewell/triage.py:511](src/triagewell/triage.py#L511) offers the fetch tool to the investigator<br>[src/triagewell/triage.py:141](src/triagewell/triage.py#L141) checks claims against real tool calls<br>[src/triagewell/gateway.py:303](src/triagewell/gateway.py#L303) leaves the tool out while the workspace has it off |
| Code execution | The reporter's Python snippet is replayed in Otari's sandbox through `POST /code/execute`, with no model in the loop, against the release the reporter had: the version they state, or the newest PyPI release before the issue was opened. The verdict is strict: reproduced only if the error the report names shows up, refuted only if the report names an error and the snippet runs cleanly, otherwise inconclusive. The settled sandbox charge is read back from Otari's usage API. | [src/triagewell/replay.py:127](src/triagewell/replay.py#L127) picks the reporter's release<br>[src/triagewell/replay.py:144](src/triagewell/replay.py#L144) installs it and runs the snippet in a fresh interpreter<br>[src/triagewell/triage.py:438](src/triagewell/triage.py#L438) sends it to the sandbox<br>[src/triagewell/triage.py:186](src/triagewell/triage.py#L186) the strict verdict<br>[src/triagewell/gateway.py:271](src/triagewell/gateway.py#L271) reads the settled charge back |
| Budgets and spending limits | A key ceiling caps the team. Every call sends a realistic output cap so Otari's hold stays small. When Otari refuses because the hold does not fit, Triagewell retries once with a cap that does; when the budget is spent, the issue is deferred with Otari's `Retry-After`. A local ledger turns away new issues before they reach the gateway. | [src/triagewell/triage.py:301](src/triagewell/triage.py#L301) retry inside the remaining budget<br>[src/triagewell/triage.py:273](src/triagewell/triage.py#L273) defer on a spent budget<br>[src/triagewell/ledger.py:39](src/triagewell/ledger.py#L39) the local daily limit |
| Multiple models serving distinct roles | A small open-weight sorter classifies and extracts search terms with reasoning off, a larger open-weight investigator reasons (effort low) with tools, and Llama Guard 4 screens the drafted reply. | [src/triagewell/config.py:93](src/triagewell/config.py#L93) the three roles<br>[src/triagewell/config.py:98](src/triagewell/config.py#L98) reasoning effort per role<br>[src/triagewell/triage.py:396](src/triagewell/triage.py#L396) sorter call<br>[src/triagewell/triage.py:616](src/triagewell/triage.py#L616) reply screen |
| Guardrails | The sorter call carries Otari's `prompt-injection` guardrail and Triagewell reads its verdict from the `Otari-Guardrails` header. A flagged issue is quarantined: no tools, no sandbox, no reply. Secrets are redacted before any model sees the issue, and the reply is checked again on the way out. On otari.ai the header reads `status: unavailable` until the organization mandates a guardrail, and Triagewell's own checks still quarantine. | [src/triagewell/triage.py:393](src/triagewell/triage.py#L393) requests the guardrail<br>[src/triagewell/gateway.py:35](src/triagewell/gateway.py#L35) reads the verdict<br>[src/triagewell/guard.py:59](src/triagewell/guard.py#L59) secret redaction |
| Per-user budgets | With a service key, Triagewell names the maintainer on every request, so each maintainer is an Otari end user with a daily budget of their own; a new maintainer can start on a trial budget, and the dashboard shows Otari's own record of their spend. It checks the key first, because otari.ai refuses `user` from an ordinary key. | [src/triagewell/triage.py:251](src/triagewell/triage.py#L251) names the maintainer only with a service key<br>[src/triagewell/gateway.py:190](src/triagewell/gateway.py#L190) names the maintainer<br>[src/triagewell/gateway.py:209](src/triagewell/gateway.py#L209) picks a new maintainer's budget<br>[src/triagewell/service.py:83](src/triagewell/service.py#L83) reads Otari's end-user record |
<!-- /feature-table -->

The report page shows, for each run, which of these features that run actually used, from the gateway's own records
(response headers and `otari_tool_calls`), so a feature that was declared but never called is not counted.

## Evidence from hosted otari.ai

On 6 Nov 2026 Triagewell triaged three real, open public issues through `https://otari.ai/api/v1`, read-only with
posting off, plus the bundled prompt-injection sample. The request ids below are the `Otari-Request-ID` headers of
those calls; each one could be read back with `GET /api/v1/usage/requests/{id}` by the key that made it, which is how
the costs below were checked. The first runs used the original ordinary key, retired once the service key worked.

| Issue | Result | Cost |
| --- | --- | --- |
| [mozilla-ai/any-agent#967](https://github.com/mozilla-ai/any-agent/issues/967) LangChain agent drops `reasoning_content` | triaged, bug, high | $0.010355 (of which the sandbox run $0.01) |
| [mozilla-ai/otari#2937](https://github.com/mozilla-ai/otari/issues/2937) hosted guardrail block mode returns 502 | triaged, bug, high | $0.000194 |
| [mozilla-ai/any-llm#1545](https://github.com/mozilla-ai/any-llm/issues/1545) Bedrock async stream reads block the event loop | triaged, medium | $0.000649 |
| bundled `prompt-injection` sample | quarantined | $0.000286 |

| Otari feature | What happened on otari.ai | Request id |
| --- | --- | --- |
| Model routing and fallbacks | any-llm#1545: the investigator asked for `openai/gpt-oss-120b`; Otari's first provider did not serve it, and Otari served the call from ovhcloud (`Otari-Fallback: true`, 2 attempts; the settled record has 2 rows). The sample's investigator call fell back the same way. Since the routing policies were created, every call asks for `triagewell-sorter` or `triagewell-investigator` by name and Otari picks the provider (nebius for the sorter, deepinfra for the investigator). | `d17edb5f-c701-438b-a8bf-8e73e137a12b`<br>`bfa71b07-7882-4280-96e9-2196874636e5`<br>policy calls: `c1a591e5-e3c1-4f82-838b-e151d2532e8b`, `7a42be13-8c94-409e-aa21-fabc3057e0c7` |
| MCP integrations | any-llm#1545 and any-agent#967: Otari's managed MCP loop called DeepWiki's `ask_wiki_question` for the repository, listed in `otari_tool_calls` as completed. | `d17edb5f-c701-438b-a8bf-8e73e137a12b`<br>`c9ecd632-1e58-43b3-89b3-1db0cc01405e` |
| Code execution | any-agent#967: the reporter's snippet ran on `POST /code/execute`; the sandbox lacks `any_agent`, and the report says "could not run" instead of claiming a reproduction. The response carries no cost, so Triagewell read the settled $0.01 back by request id. | `402d6fa2-6606-45c2-acf2-f5846f9bb22b` |
| Multiple models serving distinct roles | Every run: Nemotron 3.5 Lightning 30B A3B (fireworks) sorts, gpt-oss-120b (deepinfra or ovhcloud) investigates, Llama Guard 4 12B (deepinfra) screens the reply. otari#2937, in order: | `45e9952b-d342-425b-ba5b-f584dec987fa`<br>`6c789dd9-eb6d-40e5-a9c5-c270009ee96f`<br>`acb6d8d9-b443-4234-9af0-3483efa81ecc` |
| Guardrails | Every sorter call asked for `prompt-injection`; otari.ai answered `status: unavailable, reason: not_configured` (no organization guardrail yet). The sample was still quarantined by Triagewell's own checks: no tools, no sandbox, no reply. | `fa919570-b553-4338-909b-9dc5f9a33872` |
| Budgets and spending limits | Every call carries an output cap (sorter 600, investigator 1800, screen 20 tokens), each report records its cost from `usage.cost_usd` plus the settled sandbox and fetch charges, and the local ledger counts every run. A $2 monthly ceiling (`triagewell-team`) caps the whole workspace. | all of the above |
| Web fetch | The sample issue links the `json` docs; Triagewell read that page through `POST /fetch` before the investigator ran, fenced it as untrusted, and recorded the request id and the settled $0.01. any-llm#1584 fetched the linked provider site the same way. | `8c4ef579-456c-46e7-8476-907bc4f22e64`<br>`b174f241-9e01-4a42-8356-ef793bc26d25` |
| Per-user budgets | With the service key, a new maintainer `carol` started on the `maintainer-trial` budget ($0.015 a day; Triagewell sends the budget's Otari id, resolved from its name). Her first triage cost $0.010268 and Otari's end-user record shows exactly that. On the second, Otari refused the sandbox hold itself ("$0.004732 of budget remaining, less than this request's estimated cost of $0.01", user scope, retry in about 13 hours), and Triagewell deferred the issue instead of failing. | first run: `1f0dcb4e-6038-4441-b1f4-e4dc6e54306a`, sandbox `eca4b3ba-0815-428c-a22a-22514a5b467d`<br>refused run, sorter: `83e758d0-d9af-4de3-87f1-40635e8070dc` |

The hosted run also changed the code. The sorter now sends `reasoning_effort: none`, because Nemotron spent its whole
output cap reasoning and returned no answer. Sandbox runs are billed ($0.01 each) but answer without a cost, so the
report reads the settled amount back. The investigator retries once without tools when the CDN in front of
otari.ai times out a long tool loop (504 after about a minute), and once after a pause when every provider is busy (429).
Web fetch and per-user budgets ran once the dashboard steps in [SETUP-OTARI.md](SETUP-OTARI.md) were done (6 Nov): budgets, a workspace ceiling, a service key, both routing policies and web access. The only step left is optional: a guardrail on the organization, which needs a third-party key. Each fetched page is billed like a sandbox run ($0.01), so Triagewell fetches one page per issue by default (`TRIAGEWELL_MAX_FETCHES`).

## Models

All three roles use open-weight models from Otari's catalog. Each role is a routing policy, so the provider and the
fallbacks live in Otari, not in this code.

| Role | Policy | Model (first choice) | Fallbacks | Why this size |
| --- | --- | --- | --- | --- |
| Sorter | `triagewell-sorter` | NVIDIA Nemotron 3.5 Lightning 30B A3B | same model on a second provider, then gpt-oss-20b | Classifying and extracting search terms is easy; it runs on every issue, so it should be cheap and fast. |
| Investigator | `triagewell-investigator` | OpenAI gpt-oss-120b | same model on a second provider, then DeepSeek V4.1 Flash; gpt-oss-20b once a budget is 80% used | Tool use and reasoning over code and docs need a stronger model. |
| Reply screen | none (single model) | Meta Llama Guard 4 12B | none: if it is unavailable the screen is skipped and recorded | A safety classifier reads the drafted reply before a human sees it. |

## Safe by default

* Read-only on GitHub. Triagewell only sends GET requests to the GitHub API.
* Draft replies go to a local outbox (`.triagewell/outbox/`). Posting a comment needs all of: `TRIAGEWELL_ALLOW_POST=1`,
  the repository listed in `TRIAGEWELL_POST_REPOS`, a `GITHUB_TOKEN`, the `--post` flag, a report that was not
  quarantined, and a reply that passed the secret scan and the safety screen.
* Secrets (GitHub, AWS, OpenAI-style, Otari, Slack, Google, Hugging Face keys, private keys, JWTs, `*_TOKEN=` style
  assignments) are redacted before the issue reaches the gateway, and the reply is scanned again on the way out.
* Issue text is fenced as untrusted data in every prompt. A quarantined issue never chooses its own next step.
* Evidence the model cites from a page or the wiki that no tool call fetched is shown separately as unverified.
* Labels are limited to a fixed list, and a suggested duplicate must be one the GitHub search actually found.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```bash
git clone <this repository> triagewell
cd triagewell
uv sync
cp .env.example .env
```

Then follow [SETUP-OTARI.md](SETUP-OTARI.md) to create the otari.ai key, budgets, routing policies and tool settings
(about fifteen minutes, no card), and put the key in `.env` as `OTARI_API_KEY`. Check the setup:

```bash
uv run triagewell doctor
```

Triage a bundled sample issue (a Decimal bug with a pasted fake token), then the prompt-injection sample:

```bash
uv run triagewell triage --sample pasted-token --as alice
uv run triagewell triage --sample prompt-injection --as alice
```

Triage a real public issue, read-only:

```bash
uv run triagewell triage mozilla-ai/any-llm#1577 --as alice
uv run triagewell scan mozilla-ai/any-llm --since-hours 72 --limit 2 --as alice
```

With the service key and budgets from SETUP-OTARI.md, start a new maintainer on the small trial budget and watch Otari
stop them:

```bash
uv run triagewell triage --sample pasted-token --as carol --budget maintainer-trial
uv run triagewell triage --sample pasted-token --as carol --budget maintainer-trial
uv run triagewell budget --as carol
```

The dashboard, at http://127.0.0.1:8765:

```bash
uv run triagewell serve
```

## From your editor (MCP)

`triagewell mcp` is a stdio MCP server with read-only tools: `triage_issue`, `triage_sample`, `list_samples`,
`recent_reports`, `get_report` and `maintainer_budget`. Add it to any MCP client, for example:

```json
{
  "mcpServers": {
    "triagewell": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/triagewell", "triagewell", "mcp"]
    }
  }
}
```

## Without an otari.ai account

Otari is open source, so the whole pipeline also runs against a local gateway with small open-weight models on
[Ollama](https://ollama.com). [dev/otari-local.yml](dev/otari-local.yml) declares the same routing policies, budgets
and service key that SETUP-OTARI.md creates in the dashboard.

```bash
ollama pull qwen3:0.6b && ollama pull qwen3:1.7b && ollama pull gemma3:4b
dev/run-local-gateway.sh
```

The script clones Otari into `.otari-src/`, writes two fresh secrets to `dev/.env.local-gateway`, and serves the gateway
on port 8000. In another terminal, point `.env` at it (`OTARI_BASE_URL=http://127.0.0.1:8000/api/v1`, and
`OTARI_API_KEY` set to the `TRIAGEWELL_SERVICE_KEY` value from that file) and run the commands above. For code
execution, also run Otari's sandbox container as the script's header describes.

## Tests

```bash
uv run pytest
```

The suite runs offline: the Otari gateway and the GitHub API are replaced by fakes on `httpx.MockTransport` that answer
the way the real ones do (headers, `otari_tool_calls`, budget and guardrail refusals). It also checks that no secret
from an issue ever reaches the gateway, that GitHub is only read, and that the table above matches the code.

## Layout

| Path | What it holds |
| --- | --- |
| `src/triagewell/triage.py` | The pipeline: sort, search duplicates, run the snippet, investigate, screen |
| `src/triagewell/gateway.py` | The Otari client: chat, code execution, end users, tools, guardrail profiles |
| `src/triagewell/guard.py` | Secret redaction, injection signals, untrusted-text fencing |
| `src/triagewell/ledger.py` | Local per-maintainer spending ledger |
| `src/triagewell/github.py` | Read-only GitHub client |
| `src/triagewell/outbox.py` | Local outbox and the gated GitHub poster |
| `src/triagewell/web.py`, `mcp_server.py`, `cli.py` | The three ways in |
| `fixtures/issues/` | Sample issues (marked as samples, with fake secrets) |
| `dev/` | Local gateway config and the README table generator |

## License

Apache 2.0.
