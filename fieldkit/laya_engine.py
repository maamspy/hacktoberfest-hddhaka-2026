"""Load and warm the Laya decision model.

Laya is a 421M-parameter non-autoregressive encoder: it answers typed
`choice` / `score` / `noul` questions about a state in a single forward
pass and returns calibrated probabilities. It never generates text, so
there is nothing to parse and nothing to hallucinate.

Three things learned the hard way and encoded here:

1. Route. The English checkpoint scores 0.000 on Khmer while *staying*
   0.952 confident, so confidence gating cannot save you. `Router`
   detects the script in under 0.5ms and dispatches to the multilingual
   checkpoint instead.
2. Prefer two-option `choice` over `noul`. The English checkpoint has a
   known issue (#156) where `noul` follows its own `false:` / `true:`
   option labels instead of the state and returns a confident "no".
   A `choice` with neutral keys and the polarity in the descriptions
   avoids it.
3. Gate on `answer_confidence`, not `confidence`. This one is worth
   spelling out because the two are easy to confuse and the model card
   itself is ambiguous about it. `answer_confidence` is `max(p)` -- the
   probability mass on the answer being reported -- and it is the number
   temperature scaling fits, the number every calibration figure in the
   library is computed on, and the number the `min_confidence` gate is
   defined against (laya #361). `confidence` is a *different* quantity on
   a different scale: normalised entropy, which the library itself calls
   "not calibrated" and which does not transfer across option counts
   (#394). Gating a threshold on `confidence` selects roughly the wrong
   number of answers.

4. Use `USE_TF=0`. transformers probes for TensorFlow at import and
   TensorFlow's abseil runtime can deadlock model construction.

5. Disable xet. `huggingface_hub` 2.x fetches model weights over the new
   xet CAS protocol by default, and on some networks it fails with
   "File reconstruction error: CAS Client Error". `HF_HUB_DISABLE_XET=1`
   falls back to plain HTTP. It is read at `huggingface_hub` import time,
   so it has to be set before that import happens -- which is why it
   lives here rather than in a config file.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

# All three must be set before transformers / huggingface_hub are imported.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_router: Any = None
_load_error: str | None = None

INSTALL_HINT = (
    "Laya is not installed. Install it with:\n"
    "    pip install laya\n"
    "Fieldkit still works without it, but every decision falls to a human."
)


def available() -> bool:
    return _get_router() is not None


def load_error() -> str | None:
    return _load_error


def _get_router() -> Any:
    """Build the Router once and keep it hot."""
    global _router, _load_error

    if _router is not None:
        return _router
    if _load_error is not None:
        return None

    try:
        from laya import Router
    except Exception as exc:  # ImportError, but torch import can raise anything
        _load_error = f"{INSTALL_HINT}\n\nImport failed: {exc}"
        return None

    try:
        started = time.perf_counter()
        # Lazy by default: the router only pays for a checkpoint when a
        # language actually shows up, then keeps it resident.
        _router = Router()
        elapsed = (time.perf_counter() - started) * 1000
        print(f"[laya] router ready in {elapsed:.0f}ms", file=sys.stderr)
    except Exception as exc:
        _load_error = f"Laya failed to load: {exc}"
        return None

    return _router


def choice_questions(pairs: dict[str, str], instructions: str) -> dict:
    """Build a well-formed single-option `choice` question.

    `pairs` maps the option key to its description. Keys should be
    neutral (`a`, `b`, `blocked`) so the key text cannot leak the answer.
    """
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": pairs,
    }


def predict(state: str, questions: dict, checkpoint: str | None = None) -> dict:
    """Run one batched forward pass. All questions answer together."""
    router = _get_router()
    if router is None:
        raise RuntimeError(_load_error or "Laya unavailable")

    kwargs: dict[str, Any] = {}
    if checkpoint:
        kwargs["model"] = checkpoint

    return router.predict(state, questions, **kwargs)


def agent_for(checkpoint: str | None = None, calibration_path: str | None = None) -> Any:
    """Return the underlying `Agent`, not the Router.

    Calibration needs raw logits, and `laya.calibrate.records_from_labeled`
    reaches into the Agent to get them. The Router only exposes answers.

    If `calibration_path` points at a calibration file written by
    `Agent.save_calibration`, it is installed on the agent so the
    reported `answer_confidence` reflects the fitted temperatures.
    """
    router = _get_router()
    if router is None:
        raise RuntimeError(_load_error or "Laya unavailable")

    name = checkpoint or "english"
    try:
        agent = router.load(name)
    except Exception:
        # The Router's own name resolution may not know this checkpoint;
        # fall back to whichever agent it already has resident.
        agents = getattr(router, "_agents", {})
        if not agents:
            raise
        agent = next(iter(agents.values()))

    if calibration_path:
        import os

        if os.path.exists(calibration_path):
            try:
                agent.load_calibration(calibration_path)
            except Exception:
                # A stale or foreign calibration file must not take the
                # tool down. The shipped temperatures still apply.
                pass

    return agent


def warm(checkpoint: str | None = None) -> None:
    """Pay the cold-start cost up front so the first real call is fast."""
    _get_router()


def apply_calibration(calibration_path: str | None, checkpoint: str | None = None) -> bool:
    """Install a fitted calibration on the resident agent.

    Safe to call when no file exists or the agent is not loaded yet: it
    returns False and leaves everything alone. The shipped checkpoint
    temperatures apply either way.
    """
    if not calibration_path:
        return False
    if not os.path.exists(calibration_path):
        return False
    try:
        agent = agent_for(checkpoint, calibration_path)
    except Exception:
        return False
    return agent is not None
