# Written feedback

These are the answers to the three written questions on the submission form.
Everything in them happened while building Triagewell on otari.ai between 4 and
8 November 2026.

## When were you most tempted to skip Otari and call the provider directly? What happened?

When the code execution sandbox started refusing my requests. Triagewell replays
the snippet from a bug report in Otari's sandbox, against the version of the
package the reporter used. In my evaluation I sent 41 snippets from real closed
issues to `POST /api/v1/code/execute`. Seven came back as an HTML 403 page from
the AWS load balancer in front of otari.ai: no `Otari-Request-ID`, no JSON body,
nothing my code could tell apart from a real authorization failure. The same
snippet sent on its own ran fine. Only when I wrapped it in a short program that
installs the package with pip and runs the snippet in a fresh interpreter was it
refused, and only for some snippets. Two more runs timed out at the edge after
about a minute.

At that point running my own container, or calling a provider's code tool
directly, looked easier. I stayed because the sandbox is the one place where
untrusted code from a public issue runs, and I did not want that on my machine
or my maintainers' machines. I also did not try to work out the firewall rule or
get around it. Triagewell reports those runs as "not run, refused by the
firewall" and I wrote the problem up for the Otari team.

Second place was much smaller: when a budget refuses a request, the remaining
amount and the estimate are only in the error sentence, so I parse them with a
regular expression.

## What did you end up building yourself that Otari should've done for you?

Four things.

1. Reading back what the sandbox and the fetch cost. `POST /code/execute` and
   `POST /fetch` are each billed $0.01 but the response carries no cost, so after
   every call Triagewell looks the request id up in `GET /usage/requests/{id}`.
   It works: for 194 request ids my totals and Otari's settled records agreed to
   the sixth decimal. But a `cost_usd` on the response would save a round trip
   per call.
2. A retry that shrinks the output limit when a budget refusal says the hold,
   not the spend, does not fit. Otari explains this in the refusal text, so
   returning the remaining amount and the estimate as fields would turn that
   retry into a few lines.
3. Retries around the edge in front of the hosted gateway. Requests with a
   managed MCP tool loop (DeepWiki questions take about 19 seconds each) got a
   504 after about a minute, so the investigator retries once without tools.
   Streaming, or a longer timeout for requests that carry `mcp_servers`, would
   remove that.
4. A check that matches the evidence the model cites against the tool calls
   Otari actually ran, so a page the model says it read but never fetched is
   shown as unverified. Otari already has the data in `otari_tool_calls`. A flag
   saying which cited URLs were really fetched would help anyone writing an
   agent that produces reports.

Smaller things I hit: Nemotron 3.5 Lightning with a 300 token limit spent all of
it reasoning and returned empty content, which `reasoning_effort: none` fixed (a
note in the catalog that a model reasons by default would help). An ordinary key
that sends `user` is refused with a clear sentence but no error code. End-user
budgets are named by a generated id, so I resolve names to ids myself. And the
hosted gateway has no guardrails service of its own, so the `prompt-injection`
profile always comes back `unavailable` unless the organization brings a
third-party key.

## If the rules hadn't required Otari, would you have continued using Otari? Why or why not?

Yes. The deciding feature is per-user budgets on a service key. Triagewell acts
for several maintainers, and on otari.ai each one is an end user with a daily
budget of their own, under a ceiling for the whole workspace. When a new
maintainer on a 1.5 cent trial budget ran out, Otari itself refused the next
sandbox run and told me when the budget resets. I did not have to build any of
that. Routing policies by name mean I can change providers without touching the
code, the sandbox and fetch run outside my machine, and because Otari is open
source a project that will not send issue data to a hosted service can run the
same gateway itself.

What would make me hesitate is the edge in front of otari.ai, not Otari: the
firewall refusals on code execution and the one minute timeout were the only
times the platform got in the way.
