<p align="center"><img src="assets/logo-512.png" width="120" alt="Triagewell logo"></p>

<h1 align="center">Triagewell</h1>

<p align="center">Issue triage for open-source maintainers, run through the <a href="https://otari.ai">Otari</a> gateway on open-weight models.</p>

A maintainer points Triagewell at a new GitHub issue. A few seconds and a fraction of a cent later they get a short report:
what kind of issue it is, whether the reporter's snippet really fails (it was run in a sandbox), similar issues that may
be duplicates, what the project's docs say about the affected part, suggested labels, one next step, and a draft reply.
Nothing is posted anywhere unless the maintainer explicitly turns posting on.

Issue trackers are public inboxes, so Triagewell treats every issue as untrusted input. Secrets pasted into an issue are
removed before any model sees them. An issue that tries to give instructions to an automated reader is quarantined:
no tools, no sandbox, no reply, just a note for a human. Every maintainer has a daily budget of their own, so a flood of
spam issues cannot burn the team's money.

![A triage report in the Triagewell dashboard](assets/screenshot-report.png)

Built for Mozilla's Developers Wilding hackathon. Every model call goes through Otari, and every Otari feature below is
used for a reason the feature exists, not to tick a box.

## How it uses Otari

<!-- feature-table -->
| Otari feature | How Triagewell uses it | Where |
| --- | --- | --- |
| Model routing and fallbacks | Each role calls a routing policy by name; Otari picks the provider and falls back on failure, and tiers the investigator down when a budget is 80% used. The report records `Otari-Provider`, `Otari-Model` and `Otari-Fallback` for every call. | [src/triagewell/config.py:91](src/triagewell/config.py#L91) role names are policy names<br>[src/triagewell/gateway.py:205](src/triagewell/gateway.py#L205) reads how the call was served<br>[src/triagewell/triage.py:183](src/triagewell/triage.py#L183) a missing policy falls back to the catalog model<br>[dev/otari-local.yml:43](dev/otari-local.yml#L43) the policies, as config |
| MCP integrations | The investigator asks the repository's DeepWiki over MCP, run by Otari's managed MCP loop (inline server or a workspace-stored server id). Triagewell is also an MCP server itself, so a maintainer can triage from their editor. | [src/triagewell/triage.py:346](src/triagewell/triage.py#L346) inline MCP server on the investigator call<br>[src/triagewell/triage.py:343](src/triagewell/triage.py#L343) or the workspace's stored server<br>[src/triagewell/mcp_server.py:22](src/triagewell/mcp_server.py#L22) Triagewell's own MCP server |
| Web search or web fetch | When an issue links docs, logs or a stack trace, the investigator may read them with `otari_web_fetch`. What was really fetched comes back in `otari_tool_calls`, and evidence that cites a page nobody fetched is marked unverified. | [src/triagewell/triage.py:337](src/triagewell/triage.py#L337) declares the fetch tool<br>[src/triagewell/triage.py:115](src/triagewell/triage.py#L115) checks claims against real tool calls |
| Code execution | The reporter's Python snippet runs in Otari's sandbox through `POST /code/execute` with no model in the loop, which tells the maintainer whether the bug reproduces. The investigator can also run a short check with `otari_code_execution`. | [src/triagewell/triage.py:314](src/triagewell/triage.py#L314) runs the snippet<br>[src/triagewell/gateway.py:215](src/triagewell/gateway.py#L215) the /code/execute client<br>[src/triagewell/triage.py:340](src/triagewell/triage.py#L340) model-driven checks |
| Budgets and spending limits | A key ceiling caps the team. Every call sends a realistic output cap so Otari's hold stays small. When Otari refuses because the hold does not fit, Triagewell retries once with a cap that does; when the budget is spent, the issue is deferred with Otari's `Retry-After`. A local ledger turns away new issues before they reach the gateway. | [src/triagewell/triage.py:205](src/triagewell/triage.py#L205) retry inside the remaining budget<br>[src/triagewell/triage.py:191](src/triagewell/triage.py#L191) defer on a spent budget<br>[src/triagewell/ledger.py:39](src/triagewell/ledger.py#L39) the local daily limit |
| Multiple models serving distinct roles | A small open-weight sorter classifies and extracts search terms, a larger open-weight investigator reasons with tools, and Llama Guard 4 screens the drafted reply. | [src/triagewell/config.py:83](src/triagewell/config.py#L83) the three roles<br>[src/triagewell/triage.py:279](src/triagewell/triage.py#L279) sorter call<br>[src/triagewell/triage.py:421](src/triagewell/triage.py#L421) reply screen |
| Guardrails | The sorter call carries Otari's `prompt-injection` guardrail and Triagewell reads its verdict from the `Otari-Guardrails` header. A flagged issue is quarantined: no tools, no sandbox, no reply. Secrets are redacted before any model sees the issue, and the reply is checked again on the way out. | [src/triagewell/triage.py:276](src/triagewell/triage.py#L276) requests the guardrail<br>[src/triagewell/gateway.py:34](src/triagewell/gateway.py#L34) reads the verdict<br>[src/triagewell/guard.py:59](src/triagewell/guard.py#L59) secret redaction |
| Per-user budgets | Triagewell calls Otari with a service key and names the maintainer on every request, so each maintainer is an Otari end user with a daily budget of their own. A new maintainer can start on a trial budget, and the dashboard shows Otari's own record of their spend. | [src/triagewell/gateway.py:171](src/triagewell/gateway.py#L171) names the maintainer<br>[src/triagewell/gateway.py:190](src/triagewell/gateway.py#L190) picks a new maintainer's budget<br>[src/triagewell/service.py:80](src/triagewell/service.py#L80) reads Otari's end-user record |
<!-- /feature-table -->

The report page shows, for each run, which of these features that run actually used, from the gateway's own records
(response headers and `otari_tool_calls`), so a feature that was declared but never called is not counted.

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

Start a new maintainer on the small trial budget and watch Otari stop them:

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
