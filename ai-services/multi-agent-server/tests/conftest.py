from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


@pytest.fixture
def sample_evidence():
    return json.loads(
        (SERVICE_DIR / "samples" / "staff-shortage.json").read_text(encoding="utf-8")
    )
