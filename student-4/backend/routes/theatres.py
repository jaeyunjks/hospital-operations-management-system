"""Operating theatre board.

Answers the coordinator's question: which theatres are in use, free or
unusable right now, and what is running or scheduled next in each.
"""

from flask import Blueprint

from responses import ok
from services import database_client as dbc

bp = Blueprint("theatres", __name__)

STATE_LABELS = {
    "Out of Service": "Unusable",
    "Cleaning": "Being cleaned",
    "In Use": "In use",
    "Available": "Free",
}


@bp.get("/theatres/board")
def theatre_board():
    rows = dbc.theatre_board()
    board = []
    for row in rows:
        session = None
        if row["arrangement_id"]:
            session = {
                "arrangement_id": row["arrangement_id"],
                "procedure_name": row["procedure_name"],
                "surgeon_name": row["surgeon_name"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "status": row["session_status"],
                "patient_id": row["patient_id"],
            }
        board.append({
            "room_id": row["room_id"],
            "room_number": row["room_number"],
            "ward": row["ward"],
            "room_status": row["room_status"],
            "state_label": STATE_LABELS.get(row["room_status"], row["room_status"]),
            "bed_id": row["bed_id"],
            "bed_status": row["bed_status"],
            "session": session,
            "session_kind": (
                "current" if row["session_status"] == "In Progress"
                else "next" if row["session_status"] == "Scheduled" else None
            ),
        })

    return ok({
        "theatres": board,
        "summary": {
            "total": len(board),
            "in_use": sum(1 for t in board if t["room_status"] == "In Use"),
            "free": sum(1 for t in board if t["room_status"] == "Available"),
            "unusable": sum(1 for t in board if t["room_status"] == "Out of Service"),
            "cleaning": sum(1 for t in board if t["room_status"] == "Cleaning"),
        },
    })
