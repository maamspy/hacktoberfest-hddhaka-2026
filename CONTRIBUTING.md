# Contributing

Thank you for taking the time to contribute! This project is small on purpose, and the
maintainer reviews everything personally.

## Getting set up

```bash
git clone https://github.com/maamspy/hacktoberfest-hddhaka-2026.git
cd hacktoberfest-hddhaka-2026
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

No API key and no account is needed. The first `fieldkit` command that
touches Laya downloads the checkpoint (~808 MB English, ~647 MB
multilingual); only the one you use gets fetched.

There are two front ends over one backend:

```bash
fieldkit --help        # the CLI
streamlit run app.py   # the review console
```

`app.py` calls `fieldkit.decide` exactly like `cli.py` does. Please keep
it that way — see "What we do not want" below.

If `laya` fails to import, set `USE_TF=0` — `transformers` probes for
TensorFlow at import and TensorFlow's abseil runtime can deadlock model
construction. `fieldkit/laya_engine.py` sets it for you.

## Before you open a PR

```bash
pytest          # runs offline, no model weights needed
fieldkit config show
```

The test suite must pass. `fieldkit config show` should report
`laya_available: true` — a PR that only works with Laya missing will not
be merged, because a decision tool that silently escalates everything is
not a decision tool.

## Where things live

| Path | What belongs there |
| --- | --- |
| `fieldkit/decide.py` | The only module allowed to make a decision |
| `fieldkit/laya_engine.py` | Model loading, routing, question shapes |
| `fieldkit/<feature>.py` | One module per command group, no decisions |
| `docs/RESEARCH.md` | Research notes and the traps the design avoids |

If your change adds a new judgment call, it goes through `decide.py`. If
it does not, it does not belong in the decision path at all.

## What we do not want

- A second decision path. Two ways to decide is zero ways to reason about.
- Generative output feeding a label. Laya decides; Gemma explains.
- Anything that phones home. No telemetry, no account, no server.
- Silent fallbacks. If a model is unavailable, say so and escalate.

## Hacktoberfest

This repository participates in Hacktoberfest 2026. Issues are labelled `hacktoberfest`
and `good first issue`. Please keep PRs substantive. Generated or drive-by PRs will be
closed, and that applies here too.
