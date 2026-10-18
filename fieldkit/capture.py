"""`fieldkit capture / redact / replay` — a local log of model calls.

Capture appends a JSONL record. Redact strips obvious PII with regex and
asks Laya to flag the spans it is *not* sure about, which is what a human
reviews. Replay re-runs a captured log against a new prompt or model.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable

from fieldkit import store
from fieldkit.decide import Verdict, escalate

PII_PATTERNS = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    # Order matters. `phone` is the loosest of the numeric patterns and
    # will happily eat a card number, so the specific ones run first.
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "card": r"\b(?:\d[ -]?){13,19}\b",
    # Deliberately conservative: only match numbers that carry an explicit
    # country prefix or separators. A bare digit run is left alone, because
    # redacting every 8-digit number destroys far more than it protects.
    "phone": r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]|\d{2,4}[\s.-])\d{3,4}[\s.-]?\d{0,4}",
    "api_key": r"\b(?:sk|key|token|api)[-_][A-Za-z0-9]{16,}\b",
    "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
}

# Anything that looks like a name, an account number or a free-text ID is
# the interesting part: regex is confident, Laya is unsure, a human rules.
AMBIGUOUS_HINTS = [
    "account", "customer", "user", "client", "patient", "member", "employee",
    "name", "address", "city", "postcode", "zip", "dob", "birth", "ssn",
    "national", "passport", "contract", "invoice", "order", "ticket", "case",
]

REDACTION = "[REDACTED:{kind}]"


def redact_text(text: str, extra_patterns: Iterable[str] = ()) -> tuple[str, dict[str, int], str]:
    """Strip obvious PII. Returns the redacted text, per-kind counts, and
    the *unredacted* tail that regex could not confidently handle."""
    counts: dict[str, int] = {}
    result = text

    for kind, pattern in PII_PATTERNS.items():
        compiled = re.compile(pattern, re.IGNORECASE)

        def _sub(match: "re.Match[str]", kind: str = kind) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            return REDACTION.format(kind=kind)

        result = compiled.sub(_sub, result)

    for pattern in extra_patterns:
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue

        def _sub_custom(match: "re.Match[str]") -> str:
            counts["custom"] = counts.get("custom", 0) + 1
            return REDACTION.format(kind="custom")

        result = compiled.sub(_sub_custom, result)

    return result, counts, ambiguous_spans(result)


def ambiguous_spans(text: str) -> str:
    """The lines a human still has to look at, extracted deterministically."""
    hits = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(hint in lowered for hint in AMBIGUOUS_HINTS):
            hits.append(line.strip())
    return "\n".join(hits)


def record(
    root: Path,
    prompt: str,
    response: str,
    model: str,
    prompt_version: str = "v1",
    metadata: dict | None = None,
) -> dict:
    entry = {
        "ts": round(time.time(), 3),
        "model": model,
        "prompt_version": prompt_version,
        "prompt": prompt,
        "response": response,
        "metadata": metadata or {},
    }
    store.append_jsonl(store.path_for(root, "capture"), entry)
    return entry


def replay(root: Path, new_prompt: str | None = None, model: str | None = None,
           limit: int | None = None) -> list[dict]:
    """Re-run captured calls against a new prompt or model.

    Fieldkit does not call out to a model here: replay produces the
    request set and leaves the transport to the caller, which keeps the
    "no server, no account" promise honest.
    """
    entries = store.read_jsonl(store.path_for(root, "capture"))
    if limit:
        entries = entries[-limit:]

    return [
        {
            "ts": entry.get("ts"),
            "model": model or entry.get("model"),
            "prompt_version": entry.get("prompt_version"),
            "prompt": new_prompt if new_prompt is not None else entry.get("prompt"),
            "original_response": entry.get("response"),
        }
        for entry in entries
    ]


def queue_review(root: Path, verdicts: list[Verdict], subject: str) -> int:
    """Park the spans Laya was unsure about for a human."""
    path = store.path_for(root, "review")
    written = 0
    for verdict in verdicts:
        if not verdict.escalated:
            continue
        store.append_jsonl(path, escalate(verdict, subject, verdict.reason))
        written += 1
    return written
