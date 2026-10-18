"""Plain-file storage for Fieldkit.

Everything lives under a single workspace folder as JSONL, YAML, JSON and
Markdown, so the whole tool can be inspected, diffed and committed without
a database or a server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

WORKSPACE_ENV = "FIELDKIT_HOME"
WORKSPACE_DIRNAME = ".fieldkit"
DEFAULT_WORKSPACE = WORKSPACE_DIRNAME

LAYOUT = {
    "config": "config.json",
    "calibration": "calibration.json",
    "agent_calibration": "agent_calibration.json",
    "log": "fieldkit.log",
    "capture": "capture.jsonl",
    "review": "review.jsonl",
    "evals": "evals.jsonl",
    "evals_approved": "evals.approved.jsonl",
    "fleet": "fleet.yaml",
    "notes": "notes.md",
    "connectors": "connectors",
}


def workspace(explicit: str | None = None) -> Path:
    """Resolve the workspace root, creating it if needed.

    A path that already ends in `.fieldkit` is used as the workspace
    itself. Anything else is treated as a *project directory* and the
    workspace goes inside it, at `<project>/.fieldkit/`.

    That rule is what makes all three ways of choosing a workspace
    behave the same:

        cd /proj && fieldkit          -> /proj/.fieldkit
        fieldkit -w /proj             -> /proj/.fieldkit
        FIELDKIT_HOME=/proj fieldkit  -> /proj/.fieldkit

    Without it, `-w /proj` would silently point somewhere else from
    `cd /proj`, and the user would not know which one they were looking
    at.
    """
    root = explicit or os.environ.get(WORKSPACE_ENV) or DEFAULT_WORKSPACE
    path = Path(root).expanduser().resolve()

    if path.name != WORKSPACE_DIRNAME:
        path = path / WORKSPACE_DIRNAME

    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(root: Path, name: str) -> Path:
    """Return a named path inside the workspace, creating parent dirs."""
    rel = LAYOUT[name]
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return list(iter_jsonl(path))


def iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
