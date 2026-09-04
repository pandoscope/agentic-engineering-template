"""The gate's modules, loaded the way the gate itself loads them.

`check_gate.py` is a package-less script: it and its `gate_*` siblings
sit in one directory and import each other by bare name, which is what
`python3 scripts/ci/check_gate.py <verb>` gives them for free. Putting
that directory on `sys.path` here reproduces it, so a test patches the
same module object the gate imported rather than a private copy.

The template copy is the one under test; the root and store copies are
pinned byte-identical to it by `test_gate_copies.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_DIR = ROOT / "template" / "scripts" / "ci"

if str(GATE_DIR) not in sys.path:
    sys.path.insert(0, str(GATE_DIR))

import gate_aggregate  # noqa: E402
import gate_api  # noqa: E402
import gate_approval  # noqa: E402
import gate_leaks  # noqa: E402
import gate_payload  # noqa: E402
import gate_rerun  # noqa: E402
import gate_reviews  # noqa: E402
import gate_ticket  # noqa: E402

KEYWORDS = json.loads(
    (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "reference-keywords.json"
    ).read_text()
)

__all__ = [
    "GATE_DIR",
    "KEYWORDS",
    "ROOT",
    "gate_aggregate",
    "gate_api",
    "gate_approval",
    "gate_leaks",
    "gate_payload",
    "gate_rerun",
    "gate_reviews",
    "gate_ticket",
]
