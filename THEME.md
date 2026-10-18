# Fieldkit

## Overview

Fieldkit (working name) is a small open-source command-line tool for forward-deployed engineers (FDEs) working inside customer environments.

- **Decision rule:** each feature is either decided by Laya or left to a human (HITL).
- **Fallback:** Laya returns an answer with a confidence; anything below a threshold goes to a review queue for a human.
- **Runs:** locally, with no server, no account and no telemetry.

## The six features

Each feature gets a tiny first version. Laya handles the judgment calls and a human handles everything else.

| Feature                 | 2-hour version                                                                                                                              | Laya or human                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Environment discovery   | `discover` runs a few checks (DNS, HTTPS reach to the model endpoint, proxy and key env vars) and prints a report                           | Laya sorts each error message into blocked, auth, quota or other                  |
| Capture, redact, replay | `capture` logs a call to a local JSONL file; `redact` strips obvious PII with regex; `replay` re-runs the log against a new prompt or model | Laya flags spans it is unsure about; a human reviews those (HITL)                 |
| Customer-specific evals | `evals add` stores an expert's note as a test case; `evals run` replays them                                                                | Laya labels the note (bug, ambiguity or policy); a human approves it into the set |
| Fleet view              | `fleet.yaml` lists customer, model and prompt version; `fleet show` prints a table                                                          | Human (HITL): people maintain the file                                            |
| Legacy connectors       | One template file for a connector check; no real connectors yet                                                                             | Human (HITL)                                                                      |
| Handoff notes           | `notes add` appends to a Markdown file; `notes show` prints it                                                                              | Human (HITL)                                                                      |

Everything is stored as plain files (JSONL, YAML and Markdown) in a local folder, so there is no database or server to set up.

Sources:

- [Laya Typed-Decisions, LLM Reference](https://www.llmreference.com/model/laya-typed-decisions)
- [Laya: the 33ms open-source decision model](https://mer.vin/news/laya-the-33ms-open-source-decision-model-beating-jev/)
- [Laya open source decision model, Zimaspace](https://shop.zimaspace.com/blogs/tech-ai-hub/laya-open-source-decision-model-local-ai)
