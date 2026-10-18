# Fieldkit user guide

Everything you can do, and how to point it at the right place.

## The one concept: workspace and hub

Fieldkit has two layers.

**The workspace** is per-project: `<project>/.fieldkit/` holds that
project's review queue, eval set, capture log, notes and fleet table.
There is no database and no server.

**The hub** is global: `~/.fieldkit/` holds the register of every project
you have pointed Fieldkit at, one **central ledger** that all of them
write into, and the record of the running server.

The console is one always-on service that serves the hub. It is not
started per project — you start it once and switch between projects in
the sidebar.

```
~/.fieldkit/
  hub.json       the project register and the server record
  ledger.log     the central log, every project, in order
```

Override the hub with `FIELDKIT_HUB`.

## The central ledger

Every event is written twice: once to the project's own log, once to the
central ledger with the project name attached.

```bash
fieldkit log          # this project's log
fieldkit log --all    # every project, in one file, in order
```

```
2026-10-18 15:13:24 | acme      | note       | amin | proxy blocked
2026-10-18 15:13:24 | globex    | note       | amin | refund issued
```

This is the file to read when you want to know what happened across all
your customers, not just the one you are sitting in.

## Changing the workspace

There are three ways to pick one. **All three do the same thing**, so
there is no trap:

```bash
# 1. just be in the directory
cd /work/acme
fieldkit start

# 2. point at it with -w
fieldkit -w /work/acme start

# 3. set it for the shell
export FIELDKIT_HOME=/work/acme
fieldkit start
```

All three land on `/work/acme/.fieldkit/`.

One exception, for the rare case you want the workspace folder itself:
if the path you give already ends in `.fieldkit`, it is used as-is.

```bash
fieldkit -w /work/acme/.fieldkit start   # -> /work/acme/.fieldkit
```

### In the web console

The **Project** box in the sidebar. It lists every registered project;
pick one and the whole console switches to it. The choice is per browser
session, so you can have two tabs on two different customers.

### Checking where you are

```bash
fieldkit config show     # the workspace this command uses
fieldkit projects        # every registered project
fieldkit hub             # where the hub is, and whether the server is up
```

## The always-on console

```bash
cd /work/acme
fieldkit start
```

This registers `acme` as a project and brings the console up.

Run it again in another directory and it registers that one too, then
tells you the console is already running:

```
Registered project 'globex' -> /work/globex
Fieldkit is already running at http://localhost:8501
Project 'globex' is registered.
```

**Ports.** If something else already has 8501, Fieldkit moves to the next
free port and says so rather than failing:

```
Port 8501 was busy, using 8502 instead.
Fieldkit console at http://localhost:8502
```

If *our own* server is on the port, `fieldkit start` does not start a
second one — it just tells you where the running one is.

### Starting a new project

```bash
cd /work/new-customer
fieldkit init     # scaffolds ./.fieldkit/ and registers it
fieldkit start
```

`init` creates the folder, a `config.json`, a `fleet.yaml` with two
example rows, an empty `notes.md`, and a `README.md` inside the
workspace explaining every file in it.

### Registering a project without starting anything

```bash
fieldkit projects                        # list
fieldkit projects --remove /work/old     # unregister
```

Any Fieldkit command run in a directory registers that directory as a
project, so the list fills up as you work.

## Setting up

```bash
pip install -e /path/to/fieldkit
```

Check it works:

```bash
fieldkit config show
```

You want `"laya_available": true`. The first decision triggers a
checkpoint download (~2.5 GB) if it is not already cached; the console
and the CLI share the cache, so it only happens once.

Warm it up so the first decision is fast:

```bash
fieldkit warm
```

## The daily loop

This is the whole product in five commands:

```bash
cd /work/acme

fieldkit discover                              # is this environment usable?
fieldkit evals add --note "..." --expect bug  # capture what the expert noticed
fieldkit evals label                           # Laya proposes a label
fieldkit evals approve                         # you sign off
fieldkit evals run                             # replay the set, record accuracy
```

Everything that needs a human lands in the queue:

```bash
fieldkit review list
fieldkit review resolve 0 blocked --note "egress denied by their WAF"
```

Or do all of it in the browser:

```bash
fieldkit start
```

---

# Every feature

## `discover` — is this environment usable?

Runs real checks (DNS, TLS, proxy, credentials) and lets Laya sort each
failure into `blocked`, `auth`, `quota` or `other`.

```bash
fieldkit discover
fieldkit discover --endpoint https://api.corp.internal
fieldkit discover --no-key-check          # skip the credential check
```

```
Fieldkit discover — endpoint https://api.corp.internal

  FAIL dns: api.corp.internal does not resolve: [Errno -2] Name or service not known  [other p=0.41]
  FAIL tls: cannot reach api.corp.internal:443: [Errno -2] Name or service not known  [other p=0.41]
  ok   proxy: no proxy configured
  ok   key: GOOGLE_AI_STUDIO_API_KEY is set

2 passed, 2 failed.
2 classification(s) below the 85% threshold — queued for review.
```

A classification below the threshold is **not** reported as settled. It
goes into the review queue with a checklist, for a human. Run
`fieldkit review list` to see it.

Laya's answer replaces the built-in guess whenever it has one, and the
confidence shown is the calibrated `answer_confidence`, not the
entropy-flavoured `confidence` field.

## `capture` / `redact` / `replay` — log, scrub, re-run

**Capture** appends a model call to `capture.jsonl`.

```bash
fieldkit capture --prompt "Summarise this ticket" --response "..." --model gemma-4-26b-a4b-it
echo "your prompt" | fieldkit capture
```

**Redact** strips obvious PII with regex and asks Laya to flag the lines
regex could not confidently handle. Those go to the queue.

```bash
fieldkit redact --text "Call +44 20 7946 0958, account 8842"
echo "some transcript" | fieldkit redact
fieldkit redact --text "..." --apply      # also record it to the capture log
```

**Replay** re-runs the capture log against a new prompt or model. It
writes the request set and leaves the transport to you, so it stays
honest about making no network calls of its own.

```bash
fieldkit replay --prompt "Summarise in one line" --model gemma-4-31b-it
fieldkit replay --limit 10 --out requests.json
```

## `evals` — customer-specific tests

**add** stores an expert's note. It is _pending_, not runnable.

```bash
fieldkit evals add --note "Refund applied twice on invoice 4411." --expect bug --source "ticket 882"
```

**label** asks Laya to classify each pending note as `bug`, `ambiguity`
or `policy`. Notes it is unsure about escalate.

```bash
fieldkit evals label
```

**approve** is the human sign-off. Nothing runs until you do this.

```bash
fieldkit evals approve                # all pending
fieldkit evals approve ev_1234 ev_5678 # specific ones
fieldkit evals reject ev_1234
```

**list** shows what is where.

```bash
fieldkit evals list --status pending
fieldkit evals list --status approved
```

**run** replays the approved set and records accuracy and confidence.

```bash
fieldkit evals run
```

```
ok  ev_1792313268796: expected=bug got=bug p=0.66

runs=2 accuracy=1.0 mean_confidence=0.656
```

Results accumulate in `evals.results.jsonl`, so the counts are over
every run, not just the last one.

**calibrate** fits a temperature and an escalation threshold on your
own cases and installs it on the model.

```bash
fieldkit evals calibrate
fieldkit evals calibrate --target-error 0.05
```

Be honest about what this buys: a temperature rescales confidence but
cannot change accuracy. The library's floors are 10 cases for a
temperature and 100 in a bucket for an escalation threshold; below
those, Fieldkit says so rather than inventing a fit.

## `review` — the human-in-the-loop queue

**list** shows what needs you, with Laya's proposal, the option
distribution and the checklist.

```bash
fieldkit review list
```

```
[0] error_class — discover:dns
    proposed : other  (p=0.4079)
    reason   : confidence 0.41 < threshold 0.85
    text     : api.corp.internal does not resolve: [Errno -2] Name or service not known
    checklist:
      [ ] Reproduced the failure from this machine
      [ ] Checked the endpoint URL and host are correct
      [ ] Checked whether the environment needs a proxy or VPN
      [ ] Confirmed the key or credential is present and unexpired
      [ ] Ruled out the other three categories
```

**resolve** records your decision. The checklist ticks are optional
arguments; each one is a full checklist item string.

```bash
fieldkit review resolve 0 blocked --note "egress denied by their WAF"
fieldkit review resolve 1 auth --checked "Reproduced the failure from this machine" \
                                       "Checked the endpoint URL and host are correct"
```

In the console it is the **HITL queue** page: tick the boxes, type the
decision, press Resolve.

## `fleet` — who runs what

Human-maintained on purpose. A fleet table is a promise to a customer.

```bash
fieldkit fleet show
fieldkit fleet set acme --model gemma-4-26b-a4b-it --prompt-version v1 --owner amin --threshold 0.85
```

```
customer  model               prompt_version  threshold  owner
--------  ------------------  --------------  ---------  -----
acme      gemma-4-26b-a4b-it  v1              0.85       amin
globex    gemma-4-26b-a4b-it  v1              0.85       amin
```

`init` seeds the table with two example rows. `fleet set` on an existing
customer updates that row rather than adding a duplicate.

## `notes` — the handoff log

Markdown, append-only, never rewritten.

```bash
fieldkit notes add --text "Corporate proxy blocks 443; customer opened a ticket." --author amin
fieldkit notes show
fieldkit notes show --limit 3
```

## `connector` — connector checks

One template, human-run. Fieldkit does not run connectors.

```bash
fieldkit connector new acme-sap
fieldkit connector list
```

## `log` — the activity log

Plain text, one event per line. Written to the project's log **and** the
central ledger.

```bash
fieldkit log              # this project
fieldkit log --all        # every project, in the central ledger
fieldkit log --limit 20
fieldkit log --follow
```

```
2026-10-18 14:42:01 | decision   | note_label -> bug | p=0.65 | 7655ms
2026-10-18 14:42:01 | escalated  | note_label / eval:ev_1792313268796 | p=0.65
2026-10-18 14:42:02 | resolved   | note_label / eval:ev_1792313268796 | -> bug | checklist 1/4
```

## `projects` / `hub` — the central register

```bash
fieldkit projects
fieldkit hub
```

```
project  path
acme   * /work/acme
globex   /work/globex
```

```
Hub directory : /home/you/.fieldkit
Ledger        : /home/you/.fieldkit/ledger.log
Projects      : 2
Server        : running at http://localhost:8501 (pid 250004)
```

## `llm` — the follow-up generator

The generator only ever explains. It never changes a label.

```bash
fieldkit llm status         # which backends are available
fieldkit llm ask "What should I check here?"
fieldkit llm pull           # download a local Gemma (~4 GB, deliberate)
```

Backends are tried in order: `llama.cpp` if `llama-cli` is on PATH, a
local `transformers` model **if its weights are already cached**, then
Google AI Studio if a key is set. `fieldkit llm pull` fetches the
local model on purpose, so a button press never triggers a multi-gigabyte
download.

## `config` — settings

```bash
fieldkit config show
fieldkit config set threshold 0.85
fieldkit config set human_only true
fieldkit config set llm_backend local
fieldkit config set laya_checkpoint typed-decisions
```

| Key               | What it does                                    |
| ----------------- | ----------------------------------------------- |
| `threshold`       | Below this calibrated confidence, a human rules |
| `human_only`      | Escalate everything; useful for demos           |
| `llm_backend`     | `auto`, `local`, `api`, or a backend name       |
| `laya_checkpoint` | `english`, `multilingual`, `typed-decisions`    |
| `gemma_model`     | Which local Gemma to use                        |

## `start` — the always-on console

```bash
fieldkit start
fieldkit start --port 9000
fieldkit start --address 0.0.0.0 --open-browser
```

Nine pages: Overview, Projects, HITL queue, Reports, Log, Follow-up, Eval
cases, Notes, Fleet.

---

## The web console, page by page

| Page           | What you do there                                                         |
| -------------- | ------------------------------------------------------------------------- |
| **Overview**   | Check both models are healthy. See counts and a timeline.                 |
| **HITL queue** | Work the checklist, type the decision, press Resolve.                     |
| **Reports**    | Run a discovery probe. See eval accuracy, ECE and the fitted calibration. |
| **Log**        | The plain-text log, live.                                                 |
| **Follow-up**  | Ask Gemma a question about a queued item. Append the answer to the notes. |
| **Eval cases** | Add, label, approve, run, calibrate.                                      |
| **Notes**      | The handoff log.                                                          |
| **Fleet**      | The customer table.                                                       |

## Troubleshooting

**"Laya unavailable — everything escalates."**

```bash
fieldkit warm
```

If that fails, check `fieldkit config show` for the `laya_error` field.

**The checkpoint download fails with "CAS Client Error".**
Already handled — Fieldkit sets `HF_HUB_DISABLE_XET=1` for you.

**A follow-up answer is empty.**
gemma-4 spends a lot of its budget thinking and cannot be capped. Fieldkit
retries once at a larger budget and tells you if it still failed. Expect
around 30 seconds.

**I can't find my notes.**
`fieldkit config show` prints the resolved workspace path. You are
probably in a different directory than you think.

**Two Fieldkit instances, two different queues.**
They are in different workspaces. Check the `workspace` line in
`fieldkit config show` for both.
