"""The plain-text log.

Everything else in the workspace is structured: JSONL for records, YAML
for the fleet, Markdown for notes. That is right for machines and wrong
for a person who wants to know what happened.

This is the human-readable one. A single append-only text file, one
event per line, no rotation, no parsing. `fieldkit log` prints it.

    2026-10-18 14:30:02 | decision   | error_class  -> auth          | p=0.65 | 12ms
    2026-10-18 14:30:04 | escalated  | error_class  | acme-0        | p=0.31 | 3 of 4 options below threshold
    2026-10-18 14:30:19 | resolved   | error_class  | acme-0        | blocked | note: egress denied by their WAF
    2026-10-18 14:30:25 | followup   | acme-0       | 31s           | gemma-4-26b-a4b-it
"""

from __future__ import annotations

import time
from pathlib import Path

from fieldkit import store

WIDTH = 10


def path(root: Path) -> Path:
    return store.path_for(root, "log")


def record(root: Path, category: str, message: str, *fields: str) -> None:
    """Append one event.

    Writes to two places: the project's own log, and the central ledger
    in the hub. The project log is what that project is about; the
    ledger is the one file an FDE reads to see what happened across all
    of them.

    `fields` are plain strings; anything with a `|` in it is sanitised
    so a line can never be broken by its own content.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} | {category.ljust(WIDTH)} | {_clean(message)}"
    for field in fields:
        if field is None or field == "":
            continue
        line += f" | {_clean(str(field))}"

    store.append_text(path(root), line + "\n")

    # The central ledger, tagged with the project it came from.
    try:
        from fieldkit import hub

        project = root.parent.name or str(root)
        hub.ledger_line(f"{stamp} | {project.ljust(WIDTH)} | {category.ljust(WIDTH)} | "
                        f"{_clean(message)}" +
                        "".join(f" | {_clean(str(f))}" for f in fields
                                if f is not None and f != ""))
    except Exception:
        # A broken hub must never break a decision.
        pass


def _clean(value: str) -> str:
    return value.replace("\n", " ").replace("|", "/").strip()


def read(root: Path, limit: int | None = None) -> str:
    text = store.read_text(path(root))
    if not text.strip():
        return "No log entries yet."
    if limit:
        lines = text.rstrip("\n").split("\n")
        return "\n".join(lines[-limit:])
    return text.rstrip("\n")


def lines(root: Path) -> list[str]:
    text = store.read_text(path(root))
    return [line for line in text.splitlines() if line.strip()]
