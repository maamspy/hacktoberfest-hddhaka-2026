"""Calibration: fit temperatures and escalation thresholds on real cases.

This replaces a hand-rolled temperature fit with the one that ships in
`laya.calibrate`, for two reasons.

First, correctness. The library fits a temperature per *bucket* (question
type crossed with option count) rather than one global number, and the
library's own documentation is blunt about why: "a single `min_confidence`
does not transfer across option counts (#394): the calibrated confidence of
a 2-option and a 12-option answer live on different scales, so one cut
over- or under-abstains depending on the question."

Second, honesty about data volume. `MIN_ABSTAIN_BUCKET_N` is 100 and
`MIN_TYPE_N` is 10. A customer with three eval cases gets no fitted
abstention threshold, and this module says so instead of inventing one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fieldkit import laya_engine
from fieldkit.store import read_json, write_json

# The library's floors, mirrored so we can explain a refusal to fit.
# `MIN_BUCKET_N` is 2000: per-bucket temperatures need a lot of data, so
# a small customer dataset gets the type-level scalar and nothing more.
MIN_ABSTAIN_BUCKET_N = 100
MIN_BUCKET_N = 2000
MIN_TYPE_N = 10


def load(path: Path) -> dict:
    return read_json(path, {}) or {}


def save(path: Path, payload: dict) -> None:
    write_json(path, payload)


def _one_hot(labels: list[str], target: str):
    import numpy as np

    vector = np.zeros(len(labels), dtype=np.float64)
    if target in labels:
        vector[labels.index(target)] = 1.0
    return vector


def fit(
    cases: list[dict],
    question_for: Any,
    checkpoint: str | None = None,
    target_error: float = 0.10,
    agent_calibration_path: Path | None = None,
) -> dict:
    """Fit temperatures and per-bucket abstention thresholds on labeled cases.

    `cases` are dicts with `state` (the text) and `expected_label`.
    `question_for(labels)` returns the Laya question dict for those options.

    The fitted temperatures are installed **on the agent**, not applied
    here. The agent scales its logits by `temperature` /
    `temperature_by_options` before it reports `answer_confidence`, so
    installing them there is what makes that number calibrated. A
    temperature applied on top of the agent's own would double-count.
    """
    if len(cases) < MIN_TYPE_N:
        return {
            "fitted": False,
            "reason": (
                f"need at least {MIN_TYPE_N} labeled cases to fit a temperature, "
                f"have {len(cases)}"
            ),
        }

    agent = laya_engine.agent_for(checkpoint)

    from laya.calibrate import (
        fit_abstention_thresholds,
        records_from_labeled,
    )

    pairs = []
    for case in cases:
        labels = list(case["options"])
        questions = {"label": question_for(labels)}
        targets = {"label": _one_hot(labels, case["expected_label"])}
        pairs.append((case["state"], questions, targets))

    records = records_from_labeled(agent, pairs)
    if not records:
        return {"fitted": False, "reason": "no records could be built from those cases"}

    # fit_temperatures fits *and* installs on the agent.
    temperature_map = agent.fit_temperatures(records, compute_ece=True)

    thresholds = fit_abstention_thresholds(
        records,
        temperature_map["temperature"],
        temperature_map["temperature_by_options"],
        target_error=target_error,
        conservative=True,
    )

    if agent_calibration_path is not None:
        agent.save_calibration(str(agent_calibration_path))

    payload = {
        "fitted": True,
        "temperature": temperature_map["temperature"],
        "temperature_by_options": temperature_map["temperature_by_options"],
        "min_confidence": thresholds,
        "n_records": len(records),
        "n_by_bucket": temperature_map.get("n_by_bucket", {}),
        "temperature_clamped": temperature_map.get("temperature_clamped", {}),
        "target_error": target_error,
    }
    if "report" in temperature_map:
        payload["report"] = temperature_map["report"]

    if not thresholds:
        payload["min_confidence_note"] = (
            f"no abstention threshold fitted: every bucket needs at least "
            f"{MIN_ABSTAIN_BUCKET_N} labeled cases, and a map that omits a "
            f"bucket gates nothing. Falling back to the configured threshold."
        )
    elif "default" not in thresholds:
        payload["min_confidence_note"] = (
            "the fitted map names no 'default' bucket, so an answer whose "
            "option count falls outside the fitted buckets is not gated. "
            "Add one if your questions vary in width."
        )

    return payload
