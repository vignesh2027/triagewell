# Written feedback (draft, in Vignesh's voice)

These answer the three written questions on the submission form. They describe
what happened while building Triagewell; edit anything that does not match your
own experience, and update the CHECK line after the hosted run.

## When were you most tempted to skip Otari and call the provider directly? What happened?

When I wanted the investigator to fetch only the pages linked in the issue. With
Otari's own fetch tool I can't pass `max_uses` or allowed domains (the request is
refused), so for a moment I thought about calling a provider's native fetch, or
writing my own fetch step outside the gateway. I stayed with Otari because its
fetch already blocks private and unsafe addresses, the result comes back in
`otari_tool_calls` where I can check what was read, and the shared call limit
bounds a single request anyway. Second place was the budget refusal: I wanted the
remaining budget as a number and had to parse it out of the error text.

CHECK: if something on otari.ai itself was more tempting to route around during
the hosted run, put it first.

## What did you end up building yourself that Otari should've done for you?

Three things. First, a retry that shrinks the output limit when a budget refusal
says the hold, not the spend, is what doesn't fit. Otari already explains this in
the refusal text, so returning the remaining amount and the estimate as fields
would make that retry a few lines and not a regular expression. Second, a check
that compares the model's claimed evidence against the tool calls Otari reports,
so a page the model says it read but never fetched is marked unverified. Otari
has the data; a flag on the response saying which cited URLs were actually
fetched would help every agent that writes reports. Third, secret redaction
before the issue text reaches the model. The prompt-injection guardrail checks
whether text is trying to steer the model, but I didn't find a guardrail mode
that removes credentials from the input and still lets the request through.

## If the rules hadn't required Otari, would you have continued using Otari? Why or why not?

Yes. The reason is per-user budgets on a service key. Triagewell acts for several
maintainers, and giving each one their own daily budget, with a ceiling for the
team, took a budget and a key setting rather than a billing system of my own.
Routing policies by name also mean I can change providers without a code change,
and I can run the same gateway myself when I need to, which matters for open-source
projects that won't send issue data to a hosted service. What would make me
hesitate is the small rough edges above, not the design.

## Facts from the first hosted run (6 Nov), to fold in where they fit

* The edge in front of otari.ai answered 504 after about a minute while Otari's
  managed tool loop (DeepWiki questions take about 19 s each) was still running.
  I added a retry without tools. Streaming, or a longer edge timeout for requests
  that carry `mcp_servers`, would remove it.
* `POST /code/execute` is billed $0.01 but answers with no cost; I read it back
  from `GET /usage/requests/{id}` (otari#2864 asks for the same thing).
* Nemotron 3.5 Lightning with `max_tokens: 300` spent every token reasoning and
  returned empty content with `finish_reason: length`. `reasoning_effort: none`
  fixed it; a note in the catalog that a model reasons by default would help.
* An ordinary key that sends `user` is refused outright. Clear message, but the
  error has no `code`, so a client has to match the sentence.
* The hosted gateway has no guardrails service, so `prompt-injection` comes back
  `unavailable`, and every guardrail in the hosted catalog needs a third-party key.
* The evaluation sent 41 issue snippets to `/code/execute`. 7 got an HTML 403 from the
  AWS load balancer (no request id, no JSON), and 2 timed out at the edge. That was the
  biggest single reason the sandbox gave no verdict, more than missing packages.
* Cost tracking held up: for 194 request ids, Triagewell's own sum and Otari's settled
  records agreed to the sixth decimal.
