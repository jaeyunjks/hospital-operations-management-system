"""Ward occupancy, published as read-only context for other services.

Staff & Shift Management consumes this for staffing context and, later,
AI shift planning. It is deliberately a plain structured endpoint with no
AI involvement, so another service can depend on the numbers without
depending on a model being available.
"""

from flask import Blueprint, request

from responses import ok
from services import database_client as dbc

bp = Blueprint("wards", __name__)


def _summarise(row):
    total = row["total_beds"] or 0
    occupied = row["occupied"] or 0
    return {
        "ward": row["ward"],
        "total_beds": total,
        "occupied": occupied,
        "available": row["available"] or 0,
        "reserved": row["reserved"] or 0,
        "maintenance": row["maintenance"] or 0,
        "monitored_beds": row["monitored_beds"] or 0,
        "occupancy_pct": round(occupied * 100.0 / total, 1) if total else 0.0,
        "care_categories": sorted((row["care_categories"] or "").split(",")) if row["care_categories"] else [],
    }


@bp.get("/wards/occupancy")
def ward_occupancy():
    """Bed counts per ward. Optional ?ward= filters to one ward."""
    rows = [_summarise(r) for r in dbc.ward_occupancy(request.args.get("ward"))]

    total = sum(r["total_beds"] for r in rows)
    occupied = sum(r["occupied"] for r in rows)

    return ok({
        "wards": rows,
        "totals": {
            "total_beds": total,
            "occupied": occupied,
            "available": sum(r["available"] for r in rows),
            "reserved": sum(r["reserved"] for r in rows),
            "maintenance": sum(r["maintenance"] for r in rows),
            "occupancy_pct": round(occupied * 100.0 / total, 1) if total else 0.0,
        },
    })
