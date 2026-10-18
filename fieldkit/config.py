"""Workspace configuration and the human-in-the-loop threshold."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from fieldkit.store import read_json, write_json

DEFAULTS = {
    # Below this calibrated confidence a decision is escalated, not guessed.
    "threshold": 0.80,
    # Set true to run every command without Laya. Useful for offline demos
    # and for checking what the deterministic paths look like.
    "human_only": False,
    # Laya only reaches ~200-460ms per call on CPU, but it is never in the
    # hot path: it only gates whether a human has to look.
    "laya_checkpoint": None,
    # Which generator explains a decision to a human: "auto", "local",
    # "api", or a backend name. "auto" prefers a local model.
    "llm_backend": "auto",
    # Model for the transformers backend.
    "gemma_model": "google/gemma-4-E4B-it",
    "redact_patterns": [],
}


@dataclass
class Config:
    threshold: float = DEFAULTS["threshold"]
    human_only: bool = DEFAULTS["human_only"]
    laya_checkpoint: str | None = DEFAULTS["laya_checkpoint"]
    llm_backend: str = DEFAULTS["llm_backend"]
    gemma_model: str = DEFAULTS["gemma_model"]
    redact_patterns: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Config":
        stored = read_json(path, {}) or {}
        known = {k: v for k, v in stored.items() if k in DEFAULTS}
        return cls(**known)

    def save(self, path: Path) -> None:
        write_json(path, asdict(self))
