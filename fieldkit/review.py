"""The review queue: everything Laya was not confident enough to decide.

The queue is the safety property of the whole tool. A decision reaches a
customer environment either because Laya was confident, or because a human
picked it up here. There is no third path.
"""

from __future__ import annotations

from pathlib import Path

from fieldkit import store


def pending(root: Path) -> list[dict]:
    return [r for r in store.read_jsonl(store.path_for(root, "review")) if r.get("status") == "pending"]


def resolve(root: Path, index: int, decision: str, note: str = "",
            checked: list[str] | None = None) -> dict | None:
    """Resolve the item the human sees at `index` in `review list`.

    Index-based on purpose. Matching on content would collide the moment
    two entries share a key and subject, which they routinely do.
    """
    path = store.path_for(root, "review")
    rows = store.read_jsonl(path)

    target = None
    seen = 0
    for row in rows:
        if row.get("status") != "pending":
            continue
        if seen == index:
            target = row
            break
        seen += 1

    if target is None:
        return None

    target["status"] = "resolved"
    target["decision"] = decision
    if note:
        target["note"] = note
    if checked is not None:
        target["checked"] = checked
    store.rewrite_jsonl(path, rows)
    return target


def check_off(root: Path, index: int, item: str, done: bool = True) -> dict | None:
    """Tick or untick one checklist item on a pending item."""
    path = store.path_for(root, "review")
    rows = store.read_jsonl(path)

    target = None
    seen = 0
    for row in rows:
        if row.get("status") != "pending":
            continue
        if seen == index:
            target = row
            break
        seen += 1

    if target is None:
        return None

    checked = set(target.get("checked") or [])
    if done:
        checked.add(item)
    else:
        checked.discard(item)
    target["checked"] = sorted(checked)
    store.rewrite_jsonl(path, rows)
    return target


def resolved(root: Path) -> list[dict]:
    return [r for r in store.read_jsonl(store.path_for(root, "review")) if r.get("status") == "resolved"]


def render(rows: list[dict]) -> str:
    if not rows:
        return "Queue is empty. Nothing is waiting on a human."

    from fieldkit import checklists

    out = []
    for index, row in enumerate(rows):
        out.append(f"[{index}] {row.get('key')} — {row.get('subject')}")
        out.append(f"    proposed : {row.get('proposed')}  (p={row.get('confidence')})")
        out.append(f"    reason   : {row.get('reason')}")
        if row.get("distribution"):
            spread = "  ".join(f"{k}={v:.2f}" for k, v in row["distribution"].items())
            out.append(f"    spread   : {spread}")
        if row.get("preview"):
            out.append(f"    text     : {row.get('preview')[:160]}")
        items = row.get("checklist") or checklists.for_key(row.get("key", ""))
        if items:
            done = set(row.get("checked") or [])
            out.append("    checklist:")
            for item in items:
                mark = "x" if item in done else " "
                out.append(f"      [{mark}] {item}")
        out.append("")
    return "\n".join(out).rstrip()
