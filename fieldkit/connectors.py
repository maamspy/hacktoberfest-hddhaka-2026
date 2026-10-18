"""Connector check templates.

No real connectors ship. What ships is the shape of one: a template an
FDE copies, fills in, and runs by hand. Fieldkit refuses to guess at a
customer's connector, which is the whole point of leaving this human-owned.
"""

from __future__ import annotations

from pathlib import Path

from fieldkit import store

TEMPLATE = """# Connector check: {name}
#
# Human-owned. Fieldkit does not maintain or run connectors.
# Copy this file, fill it in, and run the steps yourself.

endpoint: https://example.invalid/api
auth: none            # none | header | basic | oauth
timeout_s: 10

steps:
  - name: health
    method: GET
    path: /health
    expect_status: 200
  - name: sample-record
    method: GET
    path: /records/1
    expect_status: 200

notes:
  - What breaks when this connector is down?
  - Who owns re-enabling it on the customer side?
"""


def template_path(root: Path, name: str) -> Path:
    target = store.path_for(root, "connectors") / f"{name}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        store.write_text(target, TEMPLATE.format(name=name))
    return target


def list_templates(root: Path) -> list[Path]:
    directory = store.path_for(root, "connectors")
    if not directory.exists():
        return []
    return sorted(directory.glob("*.yaml"))
