"""Compatible operational options for a bed shortage case.

Shared by the shortage routes and the Multi-Agent evidence builder, so the
agents see exactly the options the coordinator sees on screen.
"""

from services import database_client as dbc


def options_for(case):
    """Three kinds, in the order the architecture lists them: a bed that is
    free now, a bed pending cleaning, and a bed in an approved alternative
    ward. Escalation is always available as the last option.
    """
    category = case["required_care_category"]
    options = []

    for row in dbc.availability(care_category=category, bed_status="available"):
        if row["room_status"] == "Available" or row["room_status"] == "In Use":
            same_ward = (not case["required_ward"]) or row["ward"] == case["required_ward"]
            options.append({
                "kind": "available_now" if same_ward else "alternative_ward",
                "bed_id": row["bed_id"], "bed_number": row["bed_number"],
                "room_number": row["room_number"], "ward": row["ward"],
                "type_name": row["type_name"],
                "note": "Free now" if same_ward else
                        "Alternative ward: {}".format(row["ward"]),
            })

    for row in dbc.availability(care_category=category):
        if row["room_status"] == "Cleaning":
            options.append({
                "kind": "pending_cleaning",
                "bed_id": row["bed_id"], "bed_number": row["bed_number"],
                "room_number": row["room_number"], "ward": row["ward"],
                "type_name": row["type_name"],
                "note": "Available once cleaning completes: {}".format(row["notes"] or ""),
            })

    options.append({
        "kind": "escalate", "bed_id": None,
        "note": "Escalate to the on-call operations manager",
    })

    order = {"available_now": 0, "pending_cleaning": 1, "alternative_ward": 2, "escalate": 3}
    options.sort(key=lambda o: order[o["kind"]])
    return options
