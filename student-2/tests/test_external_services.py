"""Focused contracts for Student 2's non-mutating external lookups."""

import os
import sys


BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import external_services as external  # noqa: E402


class _Response:
    ok = True
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def test_theatre_lookup_uses_board_and_selects_only_available_theatre(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _Response({
            "success": True,
            "data": {
                "theatres": [
                    {"bed_id": 1, "room_status": "In Use", "bed_status": "occupied"},
                    {"bed_id": 2, "room_status": "Available", "bed_status": "available"},
                ]
            },
            "error": None,
        })

    monkeypatch.delenv("USE_EXTERNAL_STUBS", raising=False)
    monkeypatch.setattr(external, "SIMULATE_THEATRE", "")
    monkeypatch.setattr(external, "ROOM_BED_API_URL", "http://student-4-backend:5400/api")
    monkeypatch.setattr(external.requests, "get", fake_get)

    assert external.get_available_theatre() == {"ok": True, "bed_id": 2}
    assert calls == [("http://student-4-backend:5400/api/theatres/board", external.TIMEOUT)]
