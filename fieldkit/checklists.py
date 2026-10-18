"""Checklists for the human-in-the-loop queue.

An escalated item is not just a label waiting for a verdict -- it is a
short list of things a reviewer should actually have looked at. The
checklist is what turns "the model was unsure" into "here is what to
unsure about".

The items are per decision type and deliberately concrete. A generic
"reviewed it" checkbox is not worth clicking.
"""

from __future__ import annotations

# Keyed by the decision key. The fallback applies to anything else.
CHECKLISTS: dict[str, list[str]] = {
    "error_class": [
        "Reproduced the failure from this machine",
        "Checked the endpoint URL and host are correct",
        "Checked whether the environment needs a proxy or VPN",
        "Confirmed the key or credential is present and unexpired",
        "Ruled out the other three categories",
    ],
    "pii_review": [
        "Read every line the regex did not touch",
        "Checked for names, addresses and account identifiers",
        "Checked for credentials, keys and tokens",
        "Confirmed the redacted text is still readable and useful",
    ],
    "note_label": [
        "Read the note in full, including the links",
        "Checked the customer's policy and contract",
        "Decided whether it is a bug, an ambiguity or a policy issue",
        "Confirmed the note is worth keeping as a test case",
    ],
    "error_class_default": [],
}

FALLBACK = [
    "Read the original text in full",
    "Checked the proposed classification is the right one",
    "Confirmed the confidence in the record matches your own judgement",
    "Recorded a rationale a colleague could follow",
]


def for_key(key: str) -> list[str]:
    """The checklist for a decision key."""
    items = CHECKLISTS.get(key)
    if items is None:
        return list(FALLBACK)
    return list(items)


def is_complete(key: str, checked: list[str] | None) -> bool:
    """Every item ticked?"""
    items = for_key(key)
    if not items:
        return True
    done = set(checked or [])
    return all(item in done for item in items)


def render(key: str, checked: list[str] | None = None) -> str:
    """A plain-text checklist, for the CLI."""
    done = set(checked or [])
    lines = []
    for item in for_key(key):
        mark = "x" if item in done else " "
        lines.append(f"  [{mark}] {item}")
    return "\n".join(lines)
