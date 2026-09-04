"""Double-booking prevention.

The one piece of real domain logic in this feature. A bed or an
operating table may hold only one active arrangement at any instant, so
every create, reschedule and transfer passes through check_conflict()
before the write is attempted.

Two windows clash when each starts before the other ends. An open-ended
stay (end_time NULL) is treated as running indefinitely, which is why an
occupied inpatient bed blocks every later booking until it is released.
"""

from responses import ApiError
from services import database_client as dbc

BLOCKING_BED_STATUSES = ("maintenance",)


def find_conflicts(bed_id, start_time, end_time=None, exclude_id=None):
    """Return active arrangements clashing with the proposed window."""
    return dbc.overlapping(bed_id, start_time, end_time, exclude_id)


def check_conflict(bed_id, start_time, end_time=None, exclude_id=None):
    """Raise ApiError(409) when the proposed window is not free."""
    bed = dbc.get_record("beds", bed_id)
    if bed["status"] in BLOCKING_BED_STATUSES:
        raise ApiError(
            "Bed {} is under {} and cannot be booked".format(bed["bed_number"], bed["status"]),
            status=409,
        )

    room = dbc.get_record("rooms", bed["room_id"])
    if room["status"] == "Out of Service":
        raise ApiError(
            "Room {} is out of service and cannot be booked".format(room["room_number"]),
            status=409,
        )

    clashes = find_conflicts(bed_id, start_time, end_time, exclude_id)
    if clashes:
        first = clashes[0]
        raise ApiError(
            "Bed {} is already booked from {} to {} by arrangement {}".format(
                bed["bed_number"], first["start_time"],
                first["end_time"] or "open ended", first["arrangement_id"]),
            status=409,
        )
    return bed, room


def refresh_room_status(room_id):
    """Recompute a room's status from the beds it contains.

    A room being cleaned or out of service keeps that status: those are
    operational decisions a coordinator made, not something occupancy
    should silently overwrite.
    """
    room = dbc.get_record("rooms", room_id)
    if room["status"] in ("Cleaning", "Out of Service"):
        return room

    beds = dbc.list_records("beds", room_id=room_id)
    occupied = any(b["status"] == "occupied" for b in beds)
    wanted = "In Use" if occupied else "Available"

    if room["status"] != wanted:
        return dbc.update_record("rooms", room_id, {"status": wanted})
    return room
