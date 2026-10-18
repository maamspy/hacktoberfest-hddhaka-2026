# Fieldkit

## Overview

Fieldkit (working name) is a small open-source command-line tool for forward-deployed engineers (FDEs) working inside customer environments.

- **Decision rule:** each feature is either decided by Laya or left to a human (HITL).
- **Fallback:** Laya returns an answer with a confidence; anything below a threshold goes to a review queue for a human.
- **Runs:** locally, with no server, no account and no telemetry.

## The core idea

**Laya and Gemma are a latency ladder, not competitors.**

Laya cannot generate text; Gemma cannot report calibrated confidence.
So Fieldkit gives each one job it is actually good at:

- **Laya decides.** Every judgment call in the tool is a typed `choice`
  question answered in one forward pass, with a confidence.
- **Laya gates.** Below the threshold, nothing is applied — the case goes
  to a review queue and a human rules on it.
- **Gemma explains.** Only when a human asks why. Its prose never feeds
  back into a label, because a generator cannot produce calibrated
  probabilities and a classifier cannot produce a sentence.

Laya is never in the hot path. It runs at ~33 ms on a T4 and 193–464 ms on
CPU, which is irrelevant when every command is already doing filesystem and
network work. Gemma is slower still, and is only invoked on demand.

The follow-up agent is one-directional by design. A reviewer asks Gemma a
question about an escalated case; Gemma answers in prose; the answer goes
into the Markdown handoff notes. The label on the queue item was always
going to be written by a person — that is the premise of the tool.

## The features

Each feature gets a tiny first version. Laya handles the judgment calls and a human handles everything else.

Every escalated item carries a **checklist** of what a reviewer should
actually have looked at, per decision type. An escalated decision is not
just a label waiting for a verdict; it is a short list of things to be
unsure about. The ticks are saved and recorded alongside the decision.

| Feature                 | 2-hour version                                                                                                                              | Laya or human                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Environment discovery   | `discover` runs a few checks (DNS, HTTPS reach to the model endpoint, proxy and key env vars) and prints a report                           | Laya sorts each error message into blocked, auth, quota or other                  |
| Capture, redact, replay | `capture` logs a call to a local JSONL file; `redact` strips obvious PII with regex; `replay` re-runs the log against a new prompt or model | Laya flags spans it is unsure about; a human reviews those (HITL)                 |
| Customer-specific evals | `evals add` stores an expert's note as a test case; `evals run` replays them                                                                | Laya labels the note (bug, ambiguity or policy); a human approves it into the set |
| Fleet view              | `fleet.yaml` lists customer, model and prompt version; `fleet show` prints a table                                                          | Human (HITL): people maintain the file                                            |
| Legacy connectors       | One template file for a connector check; no real connectors yet                                                                             | Human (HITL)                                                                      |
| Handoff notes           | `notes add` appends to a Markdown file; `notes show` prints it                                                                              | Human (HITL)                                                                      |

Everything is stored as plain files (JSONL, YAML and Markdown) in a local folder, so there is no database or server to set up.

## Calibration as a first-class command

Laya ships over-confident — mean ECE 0.466 out of the box, which is worse
than useless if you gate on it. Fieldkit fits a temperature per (question
type, option count) before comparing against the threshold, and
`fieldkit evals calibrate` fits them on the customer's own results.

Honest limit: a temperature rescales confidence, it does not change
accuracy. It buys fewer bad decisions, not a better model. The real
accuracy comes from `evals` — a customer supplying real cases is the path
to a fine-tuned checkpoint.

## Status

Implemented in `fieldkit/`. See `README.md` for usage and `docs/RESEARCH.md`
for the research behind the design decisions.

Sources:

- [convaiinnovations/laya model card](https://huggingface.co/convaiinnovations/laya)
- [Laya Typed-Decisions, LLM Reference](https://www.llmreference.com/model/laya-typed-decisions)
- [Laya: the 33ms open-source decision model](https://mer.vin/news/laya-the-33ms-open-source-decision-model-beating-jev/)
- [Laya open source decision model, Zimaspace](https://shop.zimaspace.com/blogs/tech-ai-hub/laya-open-source-decision-model-local-ai)
- [Welcome Gemma 4, Hugging Face](https://huggingface.co/blog/gemma4)
- [Gemma 3n model overview, Google](https://ai.google.dev/gemma/docs/gemma-3n)
