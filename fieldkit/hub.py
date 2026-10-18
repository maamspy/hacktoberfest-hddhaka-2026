"""The central hub.

One always-on service, one ledger, many projects.

A workspace is per-project: `acme/.fieldkit/` holds acme's queue, evals
and notes. The hub is global: `~/.fieldkit/` holds the register of every
project Fieldkit has been pointed at, and one ledger that every project
writes into.

That split is the point. An FDE working across three customers has
three workspaces but one activity ledger, and one web console that
lists all three.

    ~/.fieldkit/
      hub.json        the project register and the server record
      ledger.log      the central log, every project, in order
      server.pid      the running server's pid
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

HUB_ENV = "FIELDKIT_HUB"
DEFAULT_HUB = Path.home() / ".fieldkit"
DEFAULT_PORT = 8501
SERVER_NAME = "fieldkit"


def hub_dir() -> Path:
    root = os.environ.get(HUB_ENV) or DEFAULT_HUB
    path = Path(root).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def hub_path(name: str) -> Path:
    return hub_dir() / name


def ledger_path() -> Path:
    return hub_path("ledger.log")


# --------------------------------------------------------------------------
# the project register
# --------------------------------------------------------------------------


def _load() -> dict:
    path = hub_path("hub.json")
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"projects": []}


def _save(data: dict) -> None:
    path = hub_path("hub.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def register(path: str | Path) -> dict:
    """Add a project to the register, or refresh an existing entry."""
    resolved = Path(path).expanduser().resolve()
    data = _load()
    projects = data.setdefault("projects", [])

    for entry in projects:
        if entry.get("path") == str(resolved):
            entry["seen"] = round(time.time())
            _save(data)
            return entry

    entry = {
        "path": str(resolved),
        "name": resolved.name or str(resolved),
        "registered": round(time.time()),
        "seen": round(time.time()),
    }
    projects.append(entry)
    _save(data)
    return entry


def projects() -> list[dict]:
    """Every registered project, most recently seen first."""
    data = _load()
    entries = list(data.get("projects") or [])
    entries.sort(key=lambda e: e.get("seen") or e.get("registered") or 0, reverse=True)
    return entries


def unregister(path: str | Path) -> bool:
    resolved = str(Path(path).expanduser().resolve())
    data = _load()
    before = len(data.get("projects") or [])
    data["projects"] = [e for e in data.get("projects") or [] if e.get("path") != resolved]
    _save(data)
    return len(data["projects"]) != before


# --------------------------------------------------------------------------
# the server record
# --------------------------------------------------------------------------


def server_record() -> dict | None:
    data = _load()
    record = data.get("server")
    return record if isinstance(record, dict) else None


def set_server_record(record: dict | None) -> None:
    data = _load()
    if record is None:
        data.pop("server", None)
    else:
        data["server"] = record
    _save(data)


def server_running() -> bool:
    """Is the hub server still up?"""
    record = server_record()
    if not record:
        return False
    pid = record.get("pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return port_free(record.get("port", DEFAULT_PORT)) is False


# --------------------------------------------------------------------------
# ports
# --------------------------------------------------------------------------


def port_free(port: int) -> bool:
    """Is this port free to bind?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(preferred: int = DEFAULT_PORT, attempts: int = 20) -> int:
    """The preferred port, or the next free one after it."""
    for offset in range(attempts):
        candidate = preferred + offset
        if port_free(candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------
# the central ledger
# --------------------------------------------------------------------------


def ledger_line(line: str) -> None:
    """Append a line to the central ledger."""
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line if line.endswith("\n") else line + "\n")


def read_ledger(limit: int | None = None, project: str | None = None) -> list[str]:
    """The central ledger, optionally filtered to one project."""
    try:
        with ledger_path().open(encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]
    except FileNotFoundError:
        return []

    if project:
        lines = [line for line in lines if _project_of(line) == project]

    if limit:
        lines = lines[-limit:]
    return lines


def _project_of(line: str) -> str:
    """The project column of a ledger line.

    The columns are padded for alignment, so this splits on the
    separator and strips rather than matching a fixed-width slice.
    """
    parts = line.split("|")
    if len(parts) < 3:
        return ""
    return parts[1].strip()
