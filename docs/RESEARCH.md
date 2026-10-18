# Research notes

Working notes behind the Fieldkit design. Kept here so the reasoning
survives the hackathon and can be checked against the sources.

## The two models are not competitors

The obvious framing is "open-weight model vs. hosted API, pick one".
That framing is wrong for this problem, and it is the whole idea.

**Laya** ([HF](https://huggingface.co/convaiinnovations/laya)) is a
non-autoregressive "System 1" decision model: a ModernBERT-large encoder
(395M) plus a decision head trained from scratch, 421M total. You give it
a *state* and typed *questions*; it returns typed answers with calibrated
probabilities in a single forward pass. It never generates text, so there
is nothing to parse and nothing to hallucinate. Apache 2.0.

**Gemma 4** is a generative model. It writes prose well and cannot tell
you how sure it is.

So the division of labour is not a compromise, it is the correct
decomposition:

> Laya decides whether to invoke anything, and with what label.
> Gemma explains, only when a human asks.

Critically, Gemma's output never feeds back into a label. A generator
cannot produce calibrated probabilities, and a classifier cannot produce
a sentence. Keeping them in separate roles is what keeps the confidence
numbers trustworthy.

## Measured numbers

Laya, single T4, all figures from the model card:

| | value |
| --- | --- |
| latency, 1 question | 39.5 ms (English) / 32.8 ms (multilingual) |
| latency, 10 questions batched | 158.6 ms / 72.3 ms |
| throughput | 103–332 questions/sec |
| raw ECE | 0.466 (English) / 0.314 (multilingual) |
| ECE after temperature fit | 0.081 / 0.106 |

For comparison, TypeSafe Jev is independently measured at 236–276 ms p50
for a single question. Laya is roughly 7× faster, and the weights are open.

CPU numbers, which is what actually matters here: **193–464 ms** per call
with `Router(preload=True)`. That is fine for Fieldkit, because Laya is
never in the hot path. It only answers "does this need a human?", and
every command that calls it is already doing filesystem and network work.

Gemma 4 E2B quantised, text-only, **under 1 GB of RAM** (Per-Layer
Embeddings keep the accelerator-resident footprint at ~2B params). That is
what makes local explanation possible on a laptop CPU with no GPU and no
API key — see the [Gemma 3n PLE docs](https://ai.google.dev/gemma/docs/gemma-3n#ple-caching).

## Traps, and how the code avoids them

Everything here is from the model card's own "Honest Limits" section. Each
one is a way to ship something that looks calibrated and is not.

1. **The English checkpoint collapses on non-Latin scripts** and stays
   confident while wrong — Khmer scores **0.000 accuracy at 0.952
   confidence**. No threshold can catch that. `Router` detects the script
   in <0.5 ms of pure Python and dispatches. `fieldkit/laya_engine.py`
   only ever goes through `Router`.

2. **`noul` can follow its option labels instead of the state.** It
   renders its two options as `false:` / `true:`, and that label pair can
   dominate the answer (issue #156). Fieldkit uses two-option `choice`
   with neutral keys and the polarity in the description instead.

3. **`action.act_probability` carries no usable signal** (issue #185). It
   reads 1.0 for almost every input and its logits run *against*
   correctness (AUROC 0.30). Fieldkit never reads it.

   ### The one place the model card and the library disagree

   The model card says "gate on `confidence` instead, which reaches an
   AUROC of 0.77 on the same items". The library's own documentation
   (#361, #394, and `laya/confidence.py`) says something more precise and,
   in the end, more useful:

   > `answer_confidence` is `max(p)` — the probability mass on the answer
   > being reported. It is the quantity temperature scaling fits, the
   > quantity every calibration figure in this repository is computed on,
   > and the quantity the `min_confidence` gate is defined against.

   > `confidence` is a different quantity on a different scale — normalized
   > entropy, which `common.confidence_from_probs` calls "not calibrated"
   > and #394 says does not transfer across option counts.

   So `confidence` is entropy, not a probability, and a threshold on
   entropy does not mean what a threshold on a probability means.
   Fieldkit gates on `answer_confidence` and falls back to `confidence`
   only when the former is absent, labelling which one it used in the
   reason string so the distinction is visible in the review queue.

4. **Zero-shot accuracy on typed decisions is near chance** — 0.362
   against a 0.318 random baseline. The headline 0.766 belongs to a
   fine-tuned checkpoint. This is the reason for `evals`: a customer
   supplying real cases is the path to real accuracy, and the tool tells
   you honestly how far along you are.

5. **Above ~20 options, accuracy falls off a cliff.** Options share a
   fixed `head_max_len` budget, so a 77-option question gives each label
   ~3 tokens. Every question in Fieldkit has 2–4 options by design.

6. **`score` is the weakest primitive** (0.372 on SST-5). Fieldkit avoids
   ordinal questions entirely.

7. **`USE_TF=0`.** `transformers` probes for TensorFlow at import, and
   TensorFlow's abseil runtime can deadlock model construction. Set
   unconditionally in `laya_engine.py`.

8. **`HF_HUB_DISABLE_XET=1`.** `huggingface_hub` 2.x fetches weights over
   the xet CAS protocol by default and fails with "File reconstruction
   error: CAS Client Error" on some networks. The flag is read at
   `huggingface_hub` import time, so it has to be set before that import
   — which is why it lives in `laya_engine.py` rather than in a config
   file.

## What calibration can and cannot do

Temperature scaling moves ECE from 0.466 to 0.081 on the authors' data.
That is a real gain, and `fieldkit evals calibrate` does the same fit on
your own results — using the library's own fitter rather than a
hand-rolled one, for two reasons.

**It fits per bucket, not one number.** `laya.calibrate.fit_temperature_map`
groups records by `temp_bucket(qtype, k)` — `"choice:2"`, `"choice:3-5"`,
`"choice:6-10"`, `"choice:11+"` — and fits a temperature per bucket plus a
type-level scalar for the rest. The library is blunt about why: "a single
`min_confidence` does not transfer across option counts (#394): the
calibrated confidence of a 2-option and a 12-option answer live on
different scales, so one cut over- or under-abstains depending on the
question." `fit_abstention_thresholds` then fits one cut per bucket
against a target error rate.

**It installs on the agent, which is the only place a temperature
belongs.** The agent scales its logits by `temperature` /
`temperature_by_options` before it reports `answer_confidence`. Applying
another temperature on top of that would shrink the number twice and make
the tool escalate more than the model deserves. Fieldkit's decision path
therefore reads `answer_confidence` as the agent reports it and applies no
adjustment of its own.

The library also sets floors, and Fieldkit reports them rather than
inventing a fit:

| Floor | Value | Consequence below it |
| --- | --- | --- |
| `MIN_TYPE_N` | 10 | No type-level temperature is fitted |
| `MIN_BUCKET_N` | 2000 | No per-bucket temperature is fitted |
| `MIN_ABSTAIN_BUCKET_N` | 100 | No per-bucket escalation threshold is fitted |

Read that table honestly: **a small customer dataset gets the type-level
temperature and nothing else.** Per-bucket temperatures need 2000 examples
in a bucket, and per-bucket escalation thresholds need 100. A customer
with 20 cases gets one scalar temperature for the whole `choice` type, and
falls back to the configured threshold for escalation — and Fieldkit says
exactly that rather than reporting a fit it did not perform.

An abstention map that omits a bucket **gates nothing** for that bucket,
so Fieldkit says so and falls back to the configured threshold rather than
pretending a threshold exists.

But it is worth stating the limit plainly: **a temperature rescales
confidence, it does not change accuracy.** On a model that is 60% accurate
while claiming 90% confidence, no temperature makes those numbers honest —
the best you can do is slide everything toward 0.5 and escalate more.

The real fix is more eval cases and, eventually, a fine-tuned checkpoint.

## Gemma 4, and the thinking-budget trap

`gemma-4-26b-a4b-it` is a reasoning model. Two things about it are worth
writing down because both look like a bug in your own code.

**It spends the output budget thinking.** On a request with
`maxOutputTokens: 200`, it used 239 tokens on thinking and produced an
empty answer. The response looked like a successful call with no text.
The answer part is a separate entry in `candidates[].content.parts[]`
with no `thought` flag, so filtering thought parts out is necessary but
not sufficient — you also need a budget big enough to survive the
thinking.

**You cannot turn thinking off.** `thinkingConfig: {thinkingBudget: 0}`
returns `400: Thinking budget is not supported for this model`. The only
lever is `maxOutputTokens`.

Fieldkit therefore uses a 4096-token budget and, when the visible answer
comes back empty but thinking tokens were spent, retries once at 16384.
One generous retry, not a chain: an escalating chain (4096 → 8192 → 24576)
once turned "say hello in one sentence" into a 222 second round trip,
because the model burned every budget on thinking and still had not
answered.

On CPU, a settled decision takes about 300 ms. The first call in a
process is slower because it builds the agent.

**Gemma 4 in llama.cpp.** `ggml-org/gemma-4-E2B-it-GGUF` exists and the
E2B text-only build is under 1 GB with Per-Layer Embeddings, which is
what makes a local explanation feasible on a laptop. The
Per-Layer Embeddings implementation landed in llama.cpp in July 2026
(issue #22243).

## Sources

- [convaiinnovations/laya model card](https://huggingface.co/convaiinnovations/laya)
- [Laya GitHub](https://github.com/NandhaKishorM/laya) · [docs](https://nandhakishorm.github.io/laya/)
- [Laya Typed-Decisions, LLM Reference](https://www.llmreference.com/model/laya-typed-decisions)
- [Laya: the 33ms open-source decision model, mer.vin](https://mer.vin/news/laya-the-33ms-open-source-decision-model-beating-jev/)
- [Laya open source decision model, Zimaspace](https://shop.zimaspace.com/blogs/tech-ai-hub/laya-open-source-decision-model-local-ai)
- [Laya vs Jev, flowtivity](https://flowtivity.ai/blog/laya-open-source-jev-alternative/)
- [Welcome Gemma 4, Hugging Face](https://huggingface.co/blog/gemma4)
- [Gemma 4 Technical Report](https://arxiv.org/html/2607.02770v1)
- [Gemma 3n developer guide, Google](https://developers.googleblog.com/en/introducing-gemma-3n-developer-guide/)
- [Gemma 3n model overview](https://ai.google.dev/gemma/docs/gemma-3n)
- [Run Gemma with llama.cpp](https://ai.google.dev/gemma/docs/integrations/llamacpp)
