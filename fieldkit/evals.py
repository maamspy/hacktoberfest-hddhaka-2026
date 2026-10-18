"""`fieldkit evals add / list / run` — customer-specific regression tests.

An expert writes a note about something that went wrong. Laya labels the
note as a bug, an ambiguity or a policy question; a human approves it
into the approved set before it ever runs. Nothing an expert wrote is
executed without a human saying so.
"""

from __future__ import annotations

import time
from pathlib import Path

from fieldkit import calibration, store
from fieldkit.decide import Verdict, load_calibration, save_calibration

LABELS = {
    "bug": "something was technically wrong: crash, wrong output, broken data",
    "ambiguity": "the request was unclear or under-specified; the model guessed reasonably",
    "policy": "the request was well-formed but violated a rule, contract or escalation policy",
}

EVAL_SCHEMA = {
    "expected_label": "The label this note should produce.",
}


def add(root: Path, note: str, expected: str | None = None, source: str = "") -> dict:
    entry = {
        "id": f"ev_{int(time.time() * 1000)}",
        "ts": round(time.time(), 3),
        "note": note,
        "source": source,
        "expected_label": expected,
        "status": "pending",
    }
    store.append_jsonl(store.path_for(root, "evals"), entry)
    return entry


def list_pending(root: Path) -> list[dict]:
    return [e for e in store.read_jsonl(store.path_for(root, "evals")) if e.get("status") == "pending"]


def list_approved(root: Path) -> list[dict]:
    return store.read_jsonl(store.path_for(root, "evals_approved"))


LABEL_INSTRUCTIONS = (
    "Label this field note from an expert working inside a customer "
    "environment. Choose the single category that best fits it."
)


def label(root: Path, decide_fn) -> list[tuple[dict, Verdict]]:
    """Ask Laya to label every pending note.

    `decide_fn` is the caller's single decision path, so these notes go
    through exactly the same threshold and calibration as everything else.
    """
    pending = list_pending(root)
    if not pending:
        return []

    verdicts = [
        decide_fn(
            key="note_label",
            state=entry["note"],
            pairs=LABELS,
            instructions=LABEL_INSTRUCTIONS,
        )
        for entry in pending
    ]

    return list(zip(pending, verdicts))


def approve(root: Path, ids: list[str] | None = None) -> int:
    """Move notes a human has signed off into the approved set."""
    path = store.path_for(root, "evals")
    entries = store.read_jsonl(path)
    approved_path = store.path_for(root, "evals_approved")
    wanted = set(ids) if ids else None

    remaining: list[dict] = []
    promoted = 0

    for entry in entries:
        is_pending = entry.get("status") == "pending"
        matches = wanted is None or entry.get("id") in wanted
        if is_pending and matches:
            entry["status"] = "approved"
            store.append_jsonl(approved_path, entry)
            promoted += 1
        else:
            remaining.append(entry)

    store.rewrite_jsonl(path, remaining)
    return promoted


def reject(root: Path, ids: list[str]) -> int:
    path = store.path_for(root, "evals")
    entries = store.read_jsonl(path)
    wanted = set(ids)
    kept = [e for e in entries if not (e.get("id") in wanted and e.get("status") == "pending")]
    store.rewrite_jsonl(path, kept)
    return len(entries) - len(kept)


def record_result(root: Path, case: dict, verdict: Verdict) -> dict:
    correct = verdict.answer == case.get("expected_label")
    row = {
        "id": case.get("id"),
        "ts": round(time.time(), 3),
        "expected": case.get("expected_label"),
        "predicted": verdict.answer,
        "raw_confidence": round(verdict.confidence, 4),
        "correct": bool(correct),
        "elapsed_ms": round(verdict.elapsed_ms, 1),
    }
    store.append_jsonl(store.path_for(root, "evals").with_suffix(".results.jsonl"), row)
    return row


def summarise(root: Path) -> dict:
    path = store.path_for(root, "evals").with_suffix(".results.jsonl")
    rows = store.read_jsonl(path)
    if not rows:
        return {"runs": 0}

    correct = sum(1 for row in rows if row.get("correct"))
    confidences = [float(row["raw_confidence"]) for row in rows if "raw_confidence" in row]
    flags = [bool(row.get("correct")) for row in rows if "correct" in row]

    return {
        "runs": len(rows),
        "accuracy": round(correct / len(rows), 3),
        "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
        "ece": round(
            abs(sum(confidences) / len(confidences) - correct / len(rows)), 4
        ) if flags else 0.0,
    }


def calibrate(root: Path, checkpoint: str | None = None, target_error: float = 0.10) -> dict:
    """Fit temperatures and escalation thresholds on the approved set.

    The fit needs *labeled* cases: the note and the label it should get.
    Approved eval cases carry both, because a human set the expected
    label when approving them.
    """
    approved = list_approved(root)
    cases = [
        {
            "state": case["note"],
            "expected_label": case.get("expected_label"),
            "options": list(LABELS),
        }
        for case in approved
        if case.get("expected_label") in LABELS
    ]

    if not cases:
        return {
            "fitted": False,
            "reason": "no approved case carries an expected label to fit against",
        }

    def question_for(labels: list[str]) -> dict:
        return {
            "type": "choice",
            "instructions": LABEL_INSTRUCTIONS,
            "criteria": {label: LABELS[label] for label in labels},
        }

    agent_path = store.path_for(root, "agent_calibration")
    result = calibration.fit(cases, question_for, checkpoint, target_error, agent_path)
    if not result.get("fitted"):
        return result

    calibration_path = store.path_for(root, "calibration")
    existing = load_calibration(calibration_path)
    existing.update(result)
    save_calibration(calibration_path, existing)

    result["calibration_saved"] = True
    result["calibration_path"] = str(calibration_path)
    result["agent_calibration_path"] = str(agent_path)
    return result
