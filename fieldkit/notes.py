"""`fieldkit notes add / show` — a plain Markdown handoff log.

Human-only. Notes are the thing a human hands to the next human, so
Fieldkit writes them exactly as typed and never reorders or rewrites them.
"""

from __future__ import annotations

import time
from pathlib import Path

from fieldkit import store

HEADER = "# Handoff notes\n\nOne note per entry. Newest last.\n\n"


def add(root: Path, body: str, author: str = "", tags: str = "") -> dict:
    stamp = time.strftime("%Y-%m-%d %H:%M")
    entry = {
        "ts": round(time.time(), 3),
        "author": author,
        "tags": tags,
        "body": body,
    }

    target = store.path_for(root, "notes")
    if not target.exists():
        store.write_text(target, HEADER)

    meta = f"- **{stamp}**" + (f" · _{author}_" if author else "")
    if tags:
        meta += f" · `{tags}`"

    store.append_text(target, f"{meta}\n\n{body.strip()}\n\n---\n\n")
    return entry


def show(root: Path, limit: int | None = None) -> str:
    text = store.read_text(store.path_for(root, "notes"))
    if not text.strip():
        return "No notes yet."
    if limit:
        blocks = [b for b in text.split("\n---\n") if b.strip()]
        kept = blocks[-limit:]
        header = text.split("\n---\n")[0]
        return header + "\n---\n\n".join(kept)
    return text
