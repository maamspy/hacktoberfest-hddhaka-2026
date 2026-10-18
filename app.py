"""Entry point for `streamlit run app.py`.

The console itself lives in `fieldkit/web.py` so that it can be launched
from any project directory with `fieldkit start`. This file exists so
that `streamlit run app.py` still works from the repository root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fieldkit import web  # noqa: E402,F401

web.main()
