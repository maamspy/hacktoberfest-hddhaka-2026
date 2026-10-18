"""The decision layer: ask Laya, calibrate the answer, escalate if unsure.

This is the only place in Fieldkit that decides anything. Every caller
gets back the same shape, and every answer below the calibrated threshold
is handed to a human instead of being acted on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from pathlib import Path

from fieldkit import laya_engine
from fieldkit.store import read_json, write_json

@dataclass
class Verdict:
    """The result of one decision, whatever asked for it."""

    key: str
    answer: str | None
    confidence: float
    escalated: bool
    reason: str
    elapsed_ms: float
    checkpoint: str | None = None
    distribution: dict[str, float] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def load_calibration(path: Path) -> dict:
    return read_json(path, {}) or {}


def save_calibration(path: Path, store: dict) -> None:
    write_json(path, store)


def decide(
    key: str,
    state: str,
    pairs: dict[str, str],
    instructions: str,
    threshold: float,
    calibration: dict,
    checkpoint: str | None = None,
) -> Verdict:
    """Ask Laya to pick one of `pairs`, then escalate if it is unsure."""
    if not state.strip():
        return Verdict(key, None, 0.0, True, "empty state", 0.0)

    # Laya's API keys its questions by name and answers under the same
    # name: {"answers": {"<key>": {...}}}. Passing a bare question dict
    # instead makes every answer come back empty, which silently turns
    # the whole tool into "escalate everything".
    questions = {key: laya_engine.choice_questions(pairs, instructions)}

    started = time.perf_counter()
    try:
        result = laya_engine.predict(state, questions, checkpoint=checkpoint)
    except Exception as exc:
        # Keep this to one line. It goes straight into the review queue and
        # a multi-line traceback there is unreadable in a list.
        detail = " ".join(str(exc).split())
        return Verdict(key, None, 0.0, True, f"laya error: {detail[:200]}", 0.0)
    elapsed_ms = (time.perf_counter() - started) * 1000

    answer_block = result.get("answers", {}).get(key) or {}
    choice = answer_block.get("choice")

    # `answer_confidence` is max(p) on the reported answer: the number
    # temperature scaling fits and the number the `min_confidence` gate is
    # defined against. `confidence` is normalised entropy, a different
    # quantity on a different scale that does not transfer across option
    # counts. Gate on the first; fall back to the second only if the
    # first is missing.
    raw_confidence = answer_block.get("answer_confidence")
    confidence_kind = "answer_confidence"
    if raw_confidence is None:
        raw_confidence = answer_block.get("confidence")
        confidence_kind = "confidence (entropy, not calibrated)"
    raw_confidence = float(raw_confidence or 0.0)

    # No extra temperature here on purpose. The agent already scales its
    # logits by the checkpoint's fitted per-bucket temperature before
    # reporting `answer_confidence`, so that number is already calibrated.
    # Applying another temperature on top would shrink it twice and make
    # the tool escalate more than the model deserves.
    confidence = raw_confidence

    routing = result.get("routing") or {}
    resolved = choice or next(iter(pairs))

    # The agent reports per-option probabilities under `probabilities`.
    # They are already temperature-scaled, so they are passed through as
    # they are.
    distribution = answer_block.get("probabilities")
    if isinstance(distribution, dict) and distribution:
        distribution = {k: float(v) for k, v in distribution.items()}

    # A fitted per-bucket threshold beats one global number: the calibrated
    # confidence of a 2-option and a 12-option answer live on different
    # scales, so one cut over- or under-abstains depending on the question.
    gate = _gate(answer_block, calibration, threshold)

    escalated = confidence < gate
    if escalated:
        reason = f"confidence {confidence:.2f} < threshold {gate:.2f} ({confidence_kind})"
    else:
        reason = f"confidence {confidence:.2f} >= threshold {gate:.2f} ({confidence_kind})"

    return Verdict(
        key=key,
        answer=resolved,
        confidence=confidence,
        escalated=escalated,
        reason=reason,
        elapsed_ms=elapsed_ms,
        checkpoint=routing.get("model"),
        distribution=distribution,
    )


def _gate(answer_block: dict, calibration: dict, default: float) -> float:
    """The threshold this particular answer is judged against."""
    thresholds = calibration.get("min_confidence")
    if not isinstance(thresholds, dict) or not thresholds:
        return default
    try:
        from laya.confidence import resolve_min_confidence

        return float(resolve_min_confidence(answer_block, thresholds, default))
    except Exception:
        bucket = _bucket(answer_block)
        value = thresholds.get(bucket, thresholds.get("default", default))
        return float(value)


def _bucket(answer_block: dict) -> str:
    """`choice:2`, `choice:3-5`, ... mirroring `laya.common.temp_bucket`."""
    kind = answer_block.get("type") or "choice"
    probabilities = answer_block.get("probabilities") or {}
    count = len(probabilities) or 2
    size = "2" if count <= 2 else "3-5" if count <= 5 else "6-10" if count <= 10 else "11+"
    return f"{kind}:{size}"


def escalate(verdict: Verdict, subject: str, preview: str = "") -> dict:
    """Build the review-queue row for a verdict a human has to decide."""
    from fieldkit import checklists

    return {
        "key": verdict.key,
        "subject": subject,
        "preview": preview[:280],
        "proposed": verdict.answer,
        "confidence": round(verdict.confidence, 4),
        "reason": verdict.reason,
        "elapsed_ms": round(verdict.elapsed_ms, 1),
        "checkpoint": verdict.checkpoint,
        "distribution": verdict.distribution,
        "checklist": checklists.for_key(verdict.key),
        "checked": [],
        "status": "pending",
    }


def log_decision(root, verdict: Verdict, subject: str = "") -> None:
    """Write the decision itself to the plain-text log.

    This is the "what Laya said" line. When the verdict escalates, the
    queue writes a second line saying it went to a human. Two lines, two
    different facts, no duplication.
    """
    from fieldkit import log

    log.record(
        root,
        "decision",
        f"{verdict.key} -> {verdict.answer}",
        f"p={verdict.confidence:.2f}",
        f"{verdict.elapsed_ms:.0f}ms",
        subject or None,
    )
