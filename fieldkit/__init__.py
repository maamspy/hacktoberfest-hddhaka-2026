"""Fieldkit: a local decision tool for forward-deployed engineers.

Every judgment call goes through Laya, a fast non-autoregressive decision
model. Anything Laya is not confident about is escalated to a human and
parked in a review queue instead of being guessed.

Nothing leaves the machine: no server, no account, no telemetry.
"""

__version__ = "0.1.0"
