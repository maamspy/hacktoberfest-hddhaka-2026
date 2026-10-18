# Fieldkit

A local decision tool for forward-deployed engineers working inside customer
environments.

**Laya decides. Humans review. Nothing leaves the machine.**

Fieldkit has one rule: a decision either comes from Laya with a confidence
above threshold, or it goes into a review queue for a human. There is no
third path — no silent guess, no default-on failure.

- **No server, no account, no telemetry.** Everything is plain files
  (JSONL, YAML, JSON, Markdown) in a `.fieldkit/` folder you can read,
  diff and delete.
- **Two models, two jobs.** [Laya](https://huggingface.co/convaiinnovations/laya)
  (Apache 2.0, 421M params) makes the judgment calls in ~33 ms on a T4 and
  returns calibrated probabilities. Local Gemma 4 explains a decision when
  a human asks why. Gemma never influences a label.

## Install

```bash
git clone https://github.com/maamspy/hacktoberfest-hddhaka-2026.git
cd hacktoberfest-hddhaka-2026
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

The first run downloads the Laya checkpoints (~2.5 GB for English plus
multilingual). Only the one you use gets downloaded, but `Router` keeps
both resident because it routes between them by script.

> If `laya` fails to import, set `USE_TF=0`. `transformers` probes for
> TensorFlow at import and TensorFlow's abseil runtime can deadlock model
> construction.
>
> If the checkpoint download fails with `File reconstruction error: CAS
> Client Error`, set `HF_HUB_DISABLE_XET=1` to fall back to plain HTTP
> instead of the xet storage protocol. `fieldkit/laya_engine.py` sets this
> for you.

## Run it

From any project directory:

```bash
cd /path/to/your/project
fieldkit init      # once: scaffolds ./.fieldkit/
fieldkit start     # launches the web console on http://localhost:8501
```

`fieldkit start` keeps this project's reports, queue and notes in
`./.fieldkit/`. Every project gets its own workspace, so nothing leaks
between customers.

The CLI works the same way — it picks up the current directory's
workspace automatically:

```bash
cd /path/to/your/project
fieldkit discover
fieldkit evals add --note "..." --expect bug
fieldkit review list
```

Install once, anywhere:

```bash
pip install -e /path/to/fieldkit
```

Override the workspace with `FIELDKIT_HOME` or `fieldkit -w DIR`.

**The full user guide is in [`docs/USAGE.md`](docs/USAGE.md)** — every
command, every option, and how to point Fieldkit at the right project.

## The web console

| Page | What it is |
| --- | --- |
| **Overview** | Health of both models, counts, and a timeline of everything that happened |
| **HITL queue** | Escalated decisions, each with a **checklist** of what to actually check, and a resolve button |
| **Reports** | Environment discovery report, eval accuracy and calibration, the capture log |
| **Log** | The plain-text activity log, live |
| **Follow-up** | Ask Gemma a question about any queued item; append the answer to the handoff notes |
| **Eval cases** | Add an expert note, let Laya label it, approve it, run the set, calibrate |
| **Notes** | The Markdown handoff log |
| **Fleet** | Customer / model / prompt version table (human-maintained) |

### The log

Everything else in the workspace is structured — JSONL for records, YAML
for the fleet, Markdown for notes. That is right for machines and wrong
for a person who wants to know what happened.

`fieldkit.log` is the plain-text one. Append-only, one event per line,
no rotation, no parsing:

```
2026-10-18 14:41:53 | eval added | ev_1792312913806 | expect=bug
2026-10-18 14:42:01 | decision   | note_label -> bug | p=0.65 | 7655ms
2026-10-18 14:42:01 | escalated  | note_label / eval:ev_1792312913806 | p=0.65
2026-10-18 14:42:02 | resolved   | note_label / eval:ev_1792312913806 | -> bug | checklist 1/4
2026-10-18 14:42:03 | note       | amin | Handing over to the next engineer.
2026-10-18 14:42:11 | discover   | https://api.corp.internal | 2 passed, 2 failed | dns=other
```

```bash
fieldkit log              # print it
fieldkit log --limit 20   # last 20 lines
fieldkit log --follow     # tail it
```

The **Log** tab in the web console shows the same file, and the decision
line and the escalation line are deliberately different facts: one is
what Laya said, the other is that it went to a human.

## The checklist

An escalated item is not just a label waiting for a verdict. It comes with
a short list of things a reviewer should actually have looked at, per
decision type:

```
[ ] Reproduced the failure from this machine
[ ] Checked the endpoint URL and host are correct
[ ] Checked whether the environment needs a proxy or VPN
[ ] Confirmed the key or credential is present and unexpired
[ ] Ruled out the other three categories
```

The ticks are saved, and the checklist is recorded alongside the decision
when you resolve. A generic "reviewed it" checkbox is not worth clicking,
so the items are concrete.

Nothing else is required to run the console. The console and the CLI
share one workspace, so a case escalated in either is visible in the other.

## The two models

**Laya** makes every judgment call. It is 421M parameters, runs in ~300 ms
on a CPU, and returns a calibrated probability. It never generates text.

**A generator** explains a decision to a human. It never changes a label.
`fieldkit/llm.py` picks a backend, preferring a local one:

| Backend | Needs | Notes |
| --- | --- | --- |
| `llama.cpp` | `llama-cli` on PATH | Fastest once built |
| `transformers` | `google/gemma-4-E4B-it` cached locally | Run `fieldkit llm pull` to fetch it |
| `google-ai-studio` | `GOOGLE_AI_STUDIO_API_KEY` in `.env` | Works immediately, uses the network |

```bash
fieldkit llm status    # what is available
fieldkit llm pull      # download a local Gemma (deliberately, not on a click)
fieldkit llm ask "..." # try it
```

The **decision** path is always fully local. Only the optional explanation
can use a hosted model, and `fieldkit llm status` tells you which one is
active.

### A note on gemma-4 and thinking

`gemma-4-26b-a4b-it` spends a lot of its output budget thinking, and it
does **not** support `thinkingBudget` ("Thinking budget is not supported
for this model"). A 200-token budget can be consumed entirely before the
model writes a word, which looks exactly like an empty answer. Fieldkit
uses a 4096-token budget and retries once at 16384 if the visible answer
is empty, and reports what happened rather than showing a blank.

## Use

### CLI

```bash
fieldkit discover                    # probe the environment, classify failures
fieldkit redact --text "..."         # strip obvious PII, flag the rest
fieldkit evals add --note "..." --expect bug
fieldkit evals label                 # Laya proposes a label
fieldkit evals approve               # a human signs off
fieldkit evals run                   # replay, record accuracy and confidence
fieldkit evals calibrate             # fit a temperature on your own results
fieldkit review list                 # what is waiting on a human
fieldkit review resolve 0 blocked
fieldkit fleet show
fieldkit notes show
fieldkit config show                 # effective settings + model health
```

Add `--help` to any subcommand. Point `-w/--workspace` elsewhere to work on
a specific customer.

### Review console

```bash
streamlit run app.py
```

The console is the same decision path with a UI on it — it does not add a
second way to decide.

| Page                    | What it is for                                                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **Review queue**        | The escalated cases, side by side with Laya's proposal and the full option distribution. You write the decision; Fieldkit records it. |
| **Ask a follow-up**     | You ask a question about a queued case, local Gemma answers, and you can append the exchange to the handoff notes.                    |
| **Decision playground** | Paste text, pick a task, see what Laya would decide and how confident it is.                                                          |
| **Eval cases**          | Add an expert note, let Laya label it, approve it, run the set, calibrate.                                                            |

The console and the CLI share one workspace, so a case escalated in either
place is visible in the other. The sidebar workspace field points both at
the same customer folder.

## The follow-up agent, and its one hard rule

The conversational part is deliberately **one-directional**.

Gemma answers a reviewer's question. It never changes a label, and its
prose never feeds back into a decision. A generator cannot produce
calibrated probabilities and a classifier cannot produce a sentence;
wiring one into the other's job would make the confidence numbers on the
page meaningless.

So the answer is prose for a human, and it gets appended to the Markdown
handoff notes where the next human can read it. The label on the queue
item was always going to be written by a person — that is the whole
premise of the tool.

## The features

| Command     | What it does                                                                                   | Who decides                     |
| ----------- | ---------------------------------------------------------------------------------------------- | ------------------------------- |
| `discover`  | DNS, TLS, proxy and key checks; sorts each failure into `blocked` / `auth` / `quota` / `other` | Laya, below threshold → human   |
| `capture`   | Appends a model call to `capture.jsonl`                                                        | human                           |
| `redact`    | Regex PII stripping; Laya flags what regex could not resolve                                   | Laya, below threshold → human   |
| `replay`    | Re-runs the capture log against a new prompt or model                                          | human                           |
| `evals`     | Expert notes become test cases; Laya labels them `bug` / `ambiguity` / `policy`                | Laya labels, **human approves** |
| `fleet`     | Customer / model / prompt version table                                                        | **human only**                  |
| `connector` | Connector check templates, nothing more                                                        | **human only**                  |
| `notes`     | Markdown handoff log                                                                           | **human only**                  |

The human-only features are deliberate. A fleet table is a promise to a
customer and a connector knows their infrastructure; a model that helpfully
edits either is a liability, not a feature.

## Escalation

`fieldkit/decide.py` is the only module that decides anything.

```python
verdict = decide(key="error_class", state=..., pairs=..., instructions=...)
if verdict.escalated:
    queue_for_a_human()
```

A verdict carries the answer, the calibrated confidence, the full option
distribution, which checkpoint served it and how long it took.

## Calibration

Laya's raw probabilities are badly over-confident — mean ECE 0.466 out of
the box. `fieldkit evals calibrate` fits a temperature per (question type,
option count) on your own approved cases, installs it **on the agent** so
the reported `answer_confidence` is the calibrated one, and fits a
per-bucket escalation threshold on top.

The per-bucket part is not decoration. A single threshold does not transfer
across option counts: the calibrated confidence of a 2-option and a
12-option answer live on different scales, so one cut over- or
under-abstains depending on the question.

Be honest about what this can do. A temperature rescales confidence but
cannot change accuracy. On a badly over-confident model it mostly slides
probabilities toward 0.5. It is not a substitute for more eval cases or a
fine-tuned checkpoint.

The library sets honest floors and Fieldkit reports them rather than
inventing a fit: a temperature needs 10 labeled cases, and an escalation
threshold needs 100 in a bucket. Below those, Fieldkit says so and falls
back to the configured threshold.

## Design notes worth knowing

These are the traps the Laya model card documents, and how Fieldkit avoids
them:

- **Route, never trust the English checkpoint.** It scores 0.000 accuracy
  on Khmer at 0.952 confidence. Confidence gating cannot save you; script
  detection can. `fieldkit/laya_engine.py` uses `Router` throughout, so
  Bangla and Devanagari go to the multilingual checkpoint.
- **Prefer a two-option `choice` over `noul`.** The English checkpoint has
  a known issue where `noul` follows its own `false:` / `true:` labels
  instead of the state and returns a confident "no".
- **Gate on `answer_confidence`, not `confidence`.** These are different
  numbers and the difference matters. `answer_confidence` is `max(p)` — the
  probability mass on the reported answer — and it is what temperature
  scaling fits and what the `min_confidence` gate is defined against
  (laya #361). `confidence` is *normalised entropy*: a different quantity on
  a different scale, which the library itself calls "not calibrated" and
  which does not transfer across option counts (#394). `action.act_probability`
  is worse than either — it reads ~1.0 for almost every input and its raw
  logits run against correctness.
- **`score` is the weakest primitive** (0.372 on SST-5), so Fieldkit uses
  `choice` everywhere and avoids ordinal questions.
- **Keep label spaces small.** Above ~20 options, options share a fixed
  token budget and accuracy falls off a cliff. Every question in this tool
  has 2–4 options.

See `THEME.md` for the design brief and `docs/` for the research behind it.

## Tests

```bash
pytest
```

The suite runs offline and needs no model weights.

## License

Apache 2.0.
