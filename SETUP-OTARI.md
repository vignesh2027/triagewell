# Setting up otari.ai for Triagewell

These are the dashboard steps for an otari.ai account, because otari.ai's
management pages (budgets, keys, routing, tools, guardrails, billing) take a
signed-in dashboard session and refuse an API key. No card is needed, and one
triage costs well under a tenth of a cent on the models below (the hosted runs
on 6 Nov cost $0.0002 to $0.0007 each).

I did all of this on 6 Nov from my signed-in dashboard session, through the same
management API the dashboard pages call: the budgets and the workspace ceiling,
the `triagewell-service` key, both routing policies with their fallbacks and
the 80% tier-down, and web search and fetch on the Default workspace (code
execution was already on). `uv run triagewell doctor` now reports only the
optional guardrail as missing. The MCP server registration in section 5 is also
optional: Triagewell sends DeepWiki inline and it works.

The `triagewell-team` ceiling caps the whole workspace at $2 per calendar month. Running the 54-issue evaluation again
costs about $0.40 a round; raise the ceiling first if more than a few rounds are planned.

## 1. Account and credit

Sign up at https://otari.ai/dashboard/#/signup. If you have a voucher, open
**Organization**, **Billing**, **Redeem a voucher**, paste the code, and check
the balance goes up. Do it before section 2 so the budgets have credit behind
them.

## 2. Budgets (Organization, Spend & budgets)

1. **Add budget**: name `maintainer-daily`, limit `0.25`, resets **Daily**.
2. **Add budget**: name `maintainer-trial`, limit `0.015`, resets **Daily**.
   This is the small budget a new maintainer starts on in the demo; it fits
   about one issue (a sandbox run is billed about $0.01).
3. **Add budget**: name `triagewell-team`, limit `2`, resets **Monthly**.
4. **Add ceiling**: capping your default workspace, budget `triagewell-team`.

## 3. The service key (workspace, API Keys)

An ordinary key is not enough. otari.ai refuses any request from
it that names another person in `user` ("'user' field does not match the
authenticated API key's user"), so with an ordinary key Triagewell leaves `user` out and
per-maintainer budgets stay off. To turn them on:

1. **Create key**, name it `triagewell-service`.
2. Under **Advanced**, make it a **service key**, allow the end-user budgets
   `maintainer-daily` and `maintainer-trial`, and make `maintainer-daily` the default.
3. Under **Budget (optional)** pick `triagewell-team`.
4. Put the new key (starts with `otk_`, shown once) in the repository's `.env`
   as `OTARI_API_KEY=...`, replacing any ordinary key, then archive the old key.

Triagewell detects a service key by itself (`doctor` prints `service key: yes`)
and starts naming each maintainer as an Otari end user. No code change.

## 4. Routing policies (workspace, Routing, Create policy, Fallback)

Every model below answered a test call through otari.ai on 6 Nov.

| Policy name | Main model | If unavailable, use |
| --- | --- | --- |
| `triagewell-sorter` | `nebius:nvidia/Nemotron-3_5-Lightning` | `fireworks:accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b`, then `deepinfra:openai/gpt-oss-20b` |
| `triagewell-investigator` | `deepinfra:openai/gpt-oss-120b` | `together:openai/gpt-oss-120b`, then `deepinfra:deepseek-ai/DeepSeek-V4.1-Flash-1017` |

Then open `triagewell-investigator` and add a **budget tier-down**: when budget
used is at least 80%, use `deepinfra:openai/gpt-oss-20b`.

The picker may show friendly names; pick the provider listed in the table.
Until these exist, Triagewell calls `nvidia/nemotron-3.5-lightning-30b-a3b` and
`openai/gpt-oss-120b` directly and says so in each report.

## 5. Tools (workspace, Tools)

1. **Web search**: turn **Allow** on. This also allows web fetch, which is off
   by default (`tool otari_web_fetch: available=False workspace_disabled`).
   Triagewell skips the tool while it is off rather than failing.
2. **Code execution**: on by default.
3. Optional, **MCP servers**, **Add MCP server**: name `repo_wiki`, URL
   `https://mcp.deepwiki.com/mcp`, no token, allowed tools
   `read_wiki_structure, ask_wiki_question`. Copy its id into `.env` as
   `TRIAGEWELL_MCP_SERVER_ID=...`. Without it Triagewell sends the server
   inline on each request, which already works on otari.ai.

## 6. Optional: a prompt-injection guardrail (Organization, Guardrails)

otari.ai has no guardrails service of its own: a request that asks for the
`prompt-injection` profile is served, and the `Otari-Guardrails` header says
`status: unavailable, reason: not_configured`. Every guardrail in the hosted
catalog needs a third-party key. The cheapest is Lakera Guard (free Community
tier, 10k requests a month, https://platform.lakera.ai/). If you want Otari's
own check in the demo:

1. Create a free Lakera account and an API key.
2. **Configure guardrail**: Lakera Guard, name `lakera-pi`, paste the key.
3. **Mandate a guardrail**: choose `lakera-pi`, mode **Monitor**, **If it can't
   run**: serve unchecked, every workspace. If the dialog asks for a profile
   name, use `prompt-injection`.

Skip it and Triagewell still quarantines injected issues with its own checks
(the demo's prompt-injection sample is caught that way on otari.ai today).

## 7. Check

```
cd triagewell
uv run triagewell doctor
```

It sends one tiny call per role (well under $0.001 in total), shows which
provider served each, and ends with what is still to do. Nothing it prints is
secret.
