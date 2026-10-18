"""`fieldkit fleet show / set` — which customer runs which model and prompt.

This one is human-maintained on purpose. A fleet table is a promise to
customers; a model that quietly "helpfully" edits it is a liability. Laya
does not touch this file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATE = """# Fleet: one row per customer environment.
# Human-maintained. Fieldkit never rewrites this file on its own.
fleet:
  - customer: acme
    model: gemma-4-26b-a4b-it
    prompt_version: v1
    threshold: 0.80
    owner: amin
  - customer: globex
    model: gemma-4-26b-a4b-it
    prompt_version: v1
    threshold: 0.85
    owner: amin
"""

FIELDS = ("customer", "model", "prompt_version", "threshold", "owner")


def path(root: Path) -> Path:
    return store_path(root)


def store_path(root: Path) -> Path:
    from fieldkit import store

    return store.path_for(root, "fleet")


def load(root: Path) -> list[dict]:
    from fieldkit import store

    target = store_path(root)
    if not target.exists():
        store.write_text(target, TEMPLATE)
    data = yaml.safe_load(store.read_text(target)) or {}
    rows = data.get("fleet") or []
    return [row for row in rows if isinstance(row, dict)]


def save(root: Path, rows: list[dict]) -> None:
    from fieldkit import store

    store.write_text(store_path(root), yaml.safe_dump(
        {"fleet": rows}, sort_keys=False, allow_unicode=True
    ))


def render(rows: list[dict]) -> str:
    if not rows:
        return "No customers configured."

    def cell(row: dict, key: str) -> str:
        value = row.get(key)
        return "" if value is None else str(value)

    widths = {
        key: max(len(key), max(len(cell(row, key)) for row in rows))
        for key in FIELDS
    }

    def line(values: list[str]) -> str:
        return "  ".join(value.ljust(widths[key]) for key, value in zip(FIELDS, values)).rstrip()

    out = [line(list(FIELDS)), line(["-" * widths[key] for key in FIELDS])]
    for row in rows:
        out.append(line([cell(row, key) for key in FIELDS]))
    return "\n".join(out)


def upsert(root: Path, customer: str, **updates) -> list[dict]:
    rows = load(root)
    for row in rows:
        if row.get("customer") == customer:
            row.update({k: v for k, v in updates.items() if v is not None})
            break
    else:
        rows.append({"customer": customer, **updates})
    save(root, rows)
    return rows
