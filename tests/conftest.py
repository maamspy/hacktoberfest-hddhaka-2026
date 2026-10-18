"""Test fixtures.

The single most important thing in this file: **no test may download model
weights.** A stray `Router()` in a test would pull ~808 MB from Hugging
Face and leave the next person wondering what happened to their disk and
their afternoon.

The autouse fixture below makes the Laya engine unreachable for the whole
suite, so any test that forgets to stub it gets a clean escalation
instead of a network fetch. Tests that need to exercise the decision
logic against a real model stub `laya_engine.predict` themselves.
"""

from __future__ import annotations

import pytest

from fieldkit import laya_engine


@pytest.fixture(autouse=True)
def no_model_downloads(monkeypatch):
    """Make Laya look unavailable, so nothing reaches the network."""
    monkeypatch.setattr(laya_engine, "_get_router", lambda: None)
    monkeypatch.setattr(laya_engine, "load_error", lambda: "Laya disabled during tests.")


@pytest.fixture()
def fake_laya(monkeypatch):
    """Stub the Laya engine with a scripted answer.

    Use it to test the decision path without any weights:

        def test_x(fake_laya):
            fake_laya(choice="billing", confidence=0.93)
    """
    state = {"choice": None, "confidence": 0.0, "routing": {"model": "english"}}

    def _fake_predict(_state, _questions, checkpoint=None, **_kwargs):
        return {
            "answers": {
                next(iter(_questions)): {
                    "choice": state["choice"],
                    "confidence": state["confidence"],
                    "distribution": {state["choice"]: state["confidence"]},
                }
            },
            "routing": state["routing"],
        }

    def _configure(choice=None, confidence=0.0, model="english"):
        state["choice"] = choice
        state["confidence"] = confidence
        state["routing"] = {"model": model}
        return state

    monkeypatch.setattr(laya_engine, "predict", _fake_predict)
    monkeypatch.setattr(laya_engine, "available", lambda: True)
    monkeypatch.setattr(laya_engine, "load_error", lambda: None)
    return _configure
