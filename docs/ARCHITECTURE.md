# Fieldkit

A local decision tool for forward-deployed engineers working inside customer
environments.

**Laya decides. Humans review. Nothing leaves the machine.**

Read `THEME.md` for the design brief and `docs/RESEARCH.md` for the research
behind it.

## Layout

```
app.py             thin wrapper so `streamlit run app.py` still works
fieldkit/
  web.py           the web console (dashboard, queue, reports, follow-up)
  cli.py           argparse wiring, and `fieldkit start`
  decide.py        the only module allowed to make a decision
  laya_engine.py   model loading, script routing, question shapes
  calibration.py   temperature and escalation-threshold fitting
  checklists.py    the per-decision-type HITL checklists
  discover.py      environment probes + failure classification
  capture.py       capture / redact / replay
  evals.py         customer eval cases and calibration
  fleet.py         customer / model / prompt version table (human-only)
  notes.py         Markdown handoff log (human-only)
  connectors.py    connector check templates (human-only)
  review.py        the human-in-the-loop queue
  log.py           the plain-text activity log
  explain.py       prompts for the follow-up agent
  llm.py           generator backends: llama.cpp, transformers, AI Studio
  dotenv.py        .env reader, searched from CWD then the install dir
  store.py         plain-file storage
  config.py        workspace settings and the escalation threshold
tests/             offline suite, no model weights required
docs/RESEARCH.md   research notes
```

The console and the CLI are two front ends over one backend. `web.py`
calls `fieldkit.decide` and `fieldkit.review` exactly like `cli.py` does.
If you add a decision path, it goes in `decide.py` and both front ends
pick it up. If you add a second one, the confidence numbers stop meaning
anything.

## Where the workspace lives

`fieldkit start` and the CLI both resolve the workspace relative to the
**current working directory**, so running from a customer project keeps
that project's workspace in `./.fieldkit/` and out of everyone else's
way. `FIELDKIT_HOME` overrides it, and `fieldkit -w DIR` overrides it for
a single command.

## Commands

```bash
fieldkit discover              # probe the environment, classify failures
fieldkit redact --text "..."   # strip obvious PII, flag the rest
fieldkit evals add --note "..." --expect bug
fieldkit evals label           # Laya proposes a label
fieldkit evals approve         # a human signs off
fieldkit evals run             # replay, record accuracy and confidence
fieldkit evals calibrate       # fit a temperature on your own results
fieldkit review list           # what is waiting on a human
fieldkit review resolve 0 blocked
fieldkit fleet show
fieldkit notes show
fieldkit connector new acme-sap
fieldkit config show           # effective settings + model health
fieldkit warm                  # preload Laya
```

`-w/--workspace DIR` points every command at a specific customer folder.
