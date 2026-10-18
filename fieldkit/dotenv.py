"""Minimal .env reader.

No dependency, no interpolation, no surprises: `KEY=value` lines, `#`
comments, optional `export ` prefix, optional surrounding quotes.

Two places are searched, in order: the current working directory (so a
project can carry its own key) and the directory Fieldkit was installed
from (so a global install still finds one).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = (Path.cwd() / ".env", PACKAGE_ROOT / ".env")


def parse(path: str | Path) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return {}

    loaded: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.split(" #", 1)[0].strip().strip("\"'")
        if name:
            loaded[name] = value
    return loaded


def load_dotenv(path: str | Path | None = None, override: bool = False) -> dict[str, str]:
    """Load .env into os.environ. Existing values win unless `override`.

    The first file that defines a key wins, and later files never
    overwrite an earlier one.
    """
    paths = [Path(path)] if path else list(DEFAULT_PATHS)

    loaded: dict[str, str] = {}
    for candidate in paths:
        for name, value in parse(candidate).items():
            if name in loaded:
                continue
            loaded[name] = value
            if override or name not in os.environ:
                os.environ[name] = value

    return loaded
