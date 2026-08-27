#!/usr/bin/env python3
"""Flask frontend microservice for Student 5 — Staff & Shift Management.

Workforce Overview only (first vertical slice). Renders the page shell
immediately, then HTMX requests populate it from the backend/API
microservice via server-side calls in api_client.py:

    Browser --HTMX--> this Flask app --HTTP--> backend/API (port 5500)
                                                       |
                                                       v
                                            database service (port 6500)

The browser never calls the backend directly, so no CORS configuration is
needed anywhere in this stack.

Usage:
    python3 app.py                    # serves on port 3500
    FRONTEND_PORT=4000 python3 app.py
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (Flask, make_response, render_template,  # noqa: E402
                   request, send_from_directory)

import api_client  # noqa: E402
from api_client import (BackendError, BackendUnavailableError,  # noqa: E402
                        NotFoundError)

DEFAULT_PORT = 3500
BASE_DIR = Path(__file__).resolve().parent
SHARED_FRONTEND_DIR = BASE_DIR.parents[1] / "shared" / "frontend"


def _today() -> str:
    """ISO date — used for API filters and machine-readable display."""
    return datetime.date.today().isoformat()


def _today_long() -> str:
    """Human-readable date for the page header (e.g. "Wed, 26 Aug 2026")."""
    # %-d is POSIX-only but this service targets Linux/macOS containers;
    # lstrip keeps it correct without relying on the platform extension.
    today = datetime.date.today()
    return today.strftime("%a, %d %b %Y").replace(" 0", " ")


def _display_id(staff_id: int) -> str:
    """Deterministic display identifier. The real numeric primary key is what
    every API call uses; this is presentation only and is not an external HR
    identifier."""
    return "S-%03d" % staff_id


def _shift_display_id(shift_id: int) -> str:
    """Deterministic presentation ID; API calls still use the integer key."""
    return "SH-%03d" % shift_id


def _valid_iso_date(value: str) -> bool:
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _date_long(value: str) -> str:
    """Format an ISO date without losing the stored value on bad input."""
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%a, %d %b %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return value


def _coverage(assigned: int, required: int):
    """Return display text and badge class from real assignment counts."""
    difference = assigned - required
    if difference == 0:
        return {"label": "Fully staffed", "badge": "success", "difference": 0}
    if difference < 0:
        return {"label": f"Gap {abs(difference)}", "badge": "warning",
                "difference": difference}
    return {"label": f"Overstaffed by {difference}", "badge": "info",
            "difference": difference}


def _planning_coverage(assigned: int, required: int):
    """Return the planner's compact status from assignment arithmetic."""
    if required <= 0:
        return {"label": "No demand", "tone": "neutral", "gap": 0}
    gap = max(required - assigned, 0)
    if gap == 0:
        return {"label": "Covered", "tone": "covered", "gap": 0}
    if gap == 1:
        return {"label": "Short 1", "tone": "short-one", "gap": 1}
    return {"label": "Short 2+", "tone": "short-many", "gap": gap}


def _time_minutes(value: str) -> int:
    """Convert the stored HH:MM time to minutes after midnight."""
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour * 60 + minute
    except (AttributeError, TypeError, ValueError):
        return 0


def _shift_period(start_time: str) -> str:
    """Presentation-only period label; exact stored times stay visible."""
    minute = _time_minutes(start_time)
    if minute < 5 * 60:
        return "Night"
    if minute < 12 * 60:
        return "Morning"
    if minute < 17 * 60:
        return "Afternoon"
    if minute < 22 * 60:
        return "Evening"
    return "Night"


def _week_start_for(value: str) -> datetime.date:
    """Return the Monday containing an ISO date, falling back to today."""
    try:
        day = datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        day = datetime.date.today()
    return day - datetime.timedelta(days=day.weekday())


def _aggregate_shifts(rows):
    required = sum(int(row.get("required_staff_count", 0)) for row in rows)
    assigned = sum(int(row.get("assigned_staff_count", 0)) for row in rows)
    return {
        "shift_count": len(rows),
        "required": required,
        "assigned": assigned,
        "gap": max(required - assigned, 0),
        "coverage_pct": round(assigned / required * 100) if required else None,
        "state": _planning_coverage(assigned, required),
    }


def _build_planner_model(shifts, coverage_rows, week_start_value, selected_date_value,
                         selected_department=None, required_role=None,
                         shift_status=None, view="week", now=None):
    """Build planner projections from real API rows without persisting them."""
    week_start = _week_start_for(week_start_value or selected_date_value)
    week_dates = [week_start + datetime.timedelta(days=offset) for offset in range(7)]
    week_end = week_dates[-1]

    try:
        selected_date = datetime.datetime.strptime(selected_date_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        selected_date = datetime.date.today()
    if selected_date < week_start or selected_date > week_end:
        selected_date = week_start

    coverage_by_id = {int(row["shift_id"]): row for row in coverage_rows
                      if row.get("shift_id") is not None}
    records = []
    for source in shifts:
        if required_role and source.get("required_role") != required_role:
            continue
        if shift_status and source.get("shift_status") != shift_status:
            continue
        row = dict(source)
        counts = coverage_by_id.get(int(row["shift_id"]), {})
        row["assigned_staff_count"] = int(counts.get("assigned_staff_count", 0))
        row["required_staff_count"] = int(row.get("required_staff_count", 0))
        row["planning_state"] = _planning_coverage(
            row["assigned_staff_count"], row["required_staff_count"])
        row["period"] = _shift_period(row.get("start_time", ""))
        records.append(row)

    records.sort(key=lambda row: (
        row.get("shift_date", ""), row.get("start_time", ""),
        row.get("department", ""), row.get("shift_id", 0)))
    departments = sorted({row.get("department") for row in records
                          if row.get("department")})
    selected_iso = selected_date.isoformat()
    date_departments = sorted({row["department"] for row in records
                               if row.get("shift_date") == selected_iso})
    if selected_department not in departments:
        selected_department = (date_departments or departments or [""])[0]

    department_tabs = []
    for department in departments:
        department_rows = [row for row in records
                           if row.get("department") == department
                           and row.get("shift_date") == selected_iso]
        department_tabs.append({
            "name": department,
            "gap": _aggregate_shifts(department_rows)["gap"],
        })

    daily_rows = [row for row in records
                  if row.get("department") == selected_department
                  and row.get("shift_date") == selected_iso]
    daily = _aggregate_shifts(daily_rows)

    distribution = []
    time_keys = sorted({(row.get("start_time", ""), row.get("end_time", ""))
                        for row in daily_rows}, key=lambda value: _time_minutes(value[0]))
    for start_time, end_time in time_keys:
        grouped = [row for row in daily_rows
                   if row.get("start_time") == start_time and row.get("end_time") == end_time]
        distribution.append({
            "label": _shift_period(start_time),
            "start_time": start_time,
            "end_time": end_time,
            "shifts": grouped,
            **_aggregate_shifts(grouped),
        })

    week_rows = [row for row in records
                 if row.get("department") == selected_department
                 and week_start.isoformat() <= row.get("shift_date", "") <= week_end.isoformat()]
    grid_keys = sorted({(row.get("start_time", ""), row.get("end_time", ""))
                        for row in week_rows}, key=lambda value: _time_minutes(value[0]))
    grid_rows = []
    for start_time, end_time in grid_keys:
        cells = []
        for day in week_dates:
            cell_rows = [row for row in week_rows
                         if row.get("shift_date") == day.isoformat()
                         and row.get("start_time") == start_time
                         and row.get("end_time") == end_time]
            cells.append({"date": day, "shifts": cell_rows,
                          **_aggregate_shifts(cell_rows)})
        grid_rows.append({
            "label": _shift_period(start_time),
            "start_time": start_time,
            "end_time": end_time,
            "cells": cells,
        })

    previous_iso = (selected_date - datetime.timedelta(days=1)).isoformat()
    timeline_segments = []
    for row in records:
        start = _time_minutes(row.get("start_time", ""))
        end = _time_minutes(row.get("end_time", ""))
        overnight = end <= start
        segment_start = duration = None
        if row.get("shift_date") == selected_iso:
            segment_start = start
            duration = (1440 - start) if overnight else (end - start)
        elif row.get("shift_date") == previous_iso and overnight:
            segment_start = 0
            duration = end
        if duration is None or duration <= 0:
            continue
        timeline_segments.append({
            **row,
            "segment_start": segment_start,
            "segment_duration": duration,
            "left_pct": round(segment_start / 1440 * 100, 4),
            "width_pct": round(duration / 1440 * 100, 4),
            "continues_from_previous": row.get("shift_date") == previous_iso,
            "continues_next": row.get("shift_date") == selected_iso and overnight,
        })

    timeline_rows = []
    timeline_departments = sorted({row["department"] for row in timeline_segments})
    for department in timeline_departments:
        department_segments = [row for row in timeline_segments
                               if row.get("department") == department]
        department_segments.sort(key=lambda row: (row["segment_start"], row["shift_id"]))
        timeline_rows.append({
            "department": department,
            "segments": department_segments,
            **_aggregate_shifts(department_segments),
        })

    week_summary = _aggregate_shifts(week_rows)
    all_today = [row for row in records if row.get("shift_date") == selected_iso]
    unfilled = _aggregate_shifts(all_today)["gap"]
    gap_departments = len({row["department"] for row in all_today
                           if row["planning_state"]["gap"] > 0})

    current = now or datetime.datetime.now()
    now_marker = None
    if selected_date == current.date():
        minutes = current.hour * 60 + current.minute
        now_marker = {"label": current.strftime("%H:%M"),
                      "left_pct": round(minutes / 1440 * 100, 4)}

    return {
        "week_start": week_start,
        "week_end": week_end,
        "week_dates": week_dates,
        "selected_date": selected_date,
        "selected_department": selected_department,
        "departments": department_tabs,
        "daily": daily,
        "daily_rows": daily_rows,
        "distribution": distribution,
        "grid_rows": grid_rows,
        "timeline_rows": timeline_rows,
        "week_summary": week_summary,
        "unfilled": unfilled,
        "gap_departments": gap_departments,
        "now_marker": now_marker,
        "today_iso": current.date().isoformat(),
        "today_week_start": _week_start_for(current.date().isoformat()).isoformat(),
        "week_start_for_previous_day": _week_start_for(
            (selected_date - datetime.timedelta(days=1)).isoformat()).isoformat(),
        "week_start_for_next_day": _week_start_for(
            (selected_date + datetime.timedelta(days=1)).isoformat()).isoformat(),
        "view": view if view in ("week", "timeline") else "week",
        "required_role": required_role or "",
        "shift_status": shift_status or "",
    }


def _format_timestamp(value):
    """Render a stored 'YYYY-MM-DD HH:MM:SS' timestamp for people.

    Returns the raw value unchanged if it is not in the expected shape, so a
    formatting surprise can never blank out a real stored value.
    """
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt).strftime("%d %b %Y · %H:%M")
        except (ValueError, TypeError):
            continue
    return value


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p[:1].isalpha()]
    if not parts:
        return "?"
    return (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper()


#: Assignment states that no longer occupy a place on a shift.
_INACTIVE_ASSIGNMENT = ("Cancelled", "Declined")


def _split_shifts(shifts):
    """Split assigned shifts into (current, upcoming-within-7-days).

    "Current" means today's date and the clock is inside the shift window,
    including windows that cross midnight. Everything else that starts from
    today onward and within seven days is upcoming. Derived entirely from
    real shift_date/start_time/end_time — nothing is inferred beyond this.
    """
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%H:%M")
    horizon = today + datetime.timedelta(days=7)

    current, upcoming = None, []
    for shift in shifts:
        if shift.get("assignment_status") in _INACTIVE_ASSIGNMENT:
            continue
        try:
            shift_date = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue

        start, end = shift.get("start_time", ""), shift.get("end_time", "")
        overnight = bool(start and end and end < start)
        in_window = (start <= now < end) if not overnight else (now >= start or now < end)

        if shift_date == today and in_window and current is None:
            current = shift
        elif today <= shift_date <= horizon:
            upcoming.append(shift)

    upcoming.sort(key=lambda s: (s.get("shift_date", ""), s.get("start_time", "")))
    return current, upcoming


#: Display bands for the weekly grid. Presentation only — the database stores
#: real start/end times, and seeded shifts use a wider set of windows than
#: these three, so bands are matched by OVERLAP rather than equality.
WEEKLY_BANDS = (
    ("Morning", "07:00", "15:00"),
    ("Afternoon", "15:00", "23:00"),
    ("Night", "23:00", "07:00"),
)

DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")


def _minutes(value):
    hours, mins = value.split(":")
    return int(hours) * 60 + int(mins)


def _week_segments(day, start, end):
    """Absolute minute segments for a weekly period (overnight/week wrap aware)."""
    begin = day * 1440 + _minutes(start)
    length = (_minutes(end) - _minutes(start)) % 1440 or 1440
    finish = begin + length
    if finish <= 10080:
        return [(begin, finish)]
    return [(begin, 10080), (0, finish - 10080)]


def _overlaps(first, second):
    return any(a0 < b1 and b0 < a1 for a0, a1 in first for b0, b1 in second)


def _build_weekly_grid(periods, assignments, week_start):
    """Build the 3-band x 7-day grid.

    Each cell reports a state derived from two independent sources:
      * "available" — a persisted weekly availability period overlaps the band
      * "rostered"  — a real shift assignment ALSO overlaps it
      * "unavailable" — no stored period overlaps (the sparse model's meaning)

    "rostered" is never persisted: it is recomputed from shift data every
    render, so creating or cancelling an assignment never mutates the
    recurring pattern.
    """
    available = [_week_segments(p["day_of_week"], p["start_time"], p["end_time"])
                 for p in periods]

    # Map real assignments for the displayed week onto the same timeline.
    rostered = []
    for row in assignments:
        if row.get("assignment_status") in _INACTIVE_ASSIGNMENT:
            continue
        try:
            shift_date = datetime.datetime.strptime(row["shift_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue
        offset = (shift_date - week_start).days
        if not 0 <= offset <= 6:
            continue
        rostered.append(_week_segments(offset, row["start_time"], row["end_time"]))

    grid = []
    for label, start, end in WEEKLY_BANDS:
        cells = []
        for day in range(7):
            band = _week_segments(day, start, end)
            is_available = any(_overlaps(band, seg) for seg in available)
            has_shift = any(_overlaps(band, seg) for seg in rostered)
            # A real assignment with no stored availability covering it is a
            # conflict worth surfacing. Derived only — nothing extra persisted.
            if has_shift and not is_available:
                state = "conflict"
            elif has_shift:
                state = "rostered"
            elif is_available:
                state = "available"
            else:
                state = "unavailable"
            cells.append({
                "day": day,
                "day_label": DAY_LABELS[day],
                "day_name": DAY_NAMES[day],
                "state": state,
                "available": is_available,
            })
        grid.append({"band": label, "start": start, "end": end, "cells": cells})
    return grid


def _week_start(today=None):
    today = today or datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


#: Employment groupings used only to order candidates in the list. This is a
#: presentation aid, not a policy rule — the manager still decides.
_EMPLOYMENT_PRIORITY = {"Full-Time": 0, "Part-Time": 0, "Casual": 1, "Contract": 1}


def _shift_minutes_on(shift):
    """Absolute (start, end) minutes for a shift, from its own date.

    An overnight shift (end <= start) runs into the following day, so its end
    is pushed a day forward rather than being treated as invalid.
    """
    try:
        day = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return None
    base = day.toordinal() * 1440
    start = base + _time_minutes(shift["start_time"])
    end = base + _time_minutes(shift["end_time"])
    if end <= start:
        end += 1440
    return (start, end)


def _shifts_overlap(first, second):
    a, b = _shift_minutes_on(first), _shift_minutes_on(second)
    if not a or not b:
        return False
    return a[0] < b[1] and b[0] < a[1]


def _weekly_covers_shift(periods, shift):
    """True when a recurring weekly period covers the shift's whole window.

    Compared on the seven-day timeline used by the weekly grid, so overnight
    periods and the Sunday-to-Monday wrap behave consistently.
    """
    span = _shift_minutes_on(shift)
    if not span or not periods:
        return False
    try:
        day = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return False

    length = span[1] - span[0]
    start_in_week = day.weekday() * 1440 + _time_minutes(shift["start_time"])
    shift_segments = []
    end_in_week = start_in_week + length
    if end_in_week <= 10080:
        shift_segments.append((start_in_week, end_in_week))
    else:
        shift_segments.append((start_in_week, 10080))
        shift_segments.append((0, end_in_week - 10080))

    available = []
    for period in periods:
        available.extend(_week_segments(period["day_of_week"],
                                         period["start_time"], period["end_time"]))

    # Every minute of the shift must fall inside some available period.
    for seg_start, seg_end in shift_segments:
        cursor = seg_start
        progressed = True
        while cursor < seg_end and progressed:
            progressed = False
            for a_start, a_end in available:
                if a_start <= cursor < a_end:
                    cursor = min(a_end, seg_end)
                    progressed = True
                    break
        if cursor < seg_end:
            return False
    return True


def _evaluate_candidate(person, shift, assigned_ids, their_shifts, weekly_periods):
    """Deterministic eligibility for manual assignment. No scoring, no AI.

    Returns the blocking reason (if any) plus advisory notes. Blocking states
    are those the backend itself would reject or that are plainly wrong;
    weekly-availability mismatch is ADVISORY — the backend permits it, so the
    manager may still assign with the mismatch shown.
    """
    notes, blocked = [], None

    if person["staff_id"] in assigned_ids:
        blocked = "Already assigned to this shift"
    elif person.get("availability_status") == "On Leave":
        blocked = "On Leave"
    elif person.get("availability_status") == "Unavailable":
        blocked = "Unavailable"
    elif person.get("role") != shift.get("required_role"):
        blocked = "Role mismatch"
    else:
        clash = next((row for row in their_shifts
                      if row.get("shift_id") != shift.get("shift_id")
                      and row.get("assignment_status") not in _INACTIVE_ASSIGNMENT
                      and _shifts_overlap(row, shift)), None)
        if clash:
            blocked = ("Already rostered " + clash.get("shift_date", "") + " "
                       + clash.get("start_time", "") + "-" + clash.get("end_time", ""))

    covered = _weekly_covers_shift(weekly_periods, shift)
    notes.append({"ok": covered,
                  "text": "Weekly availability matches" if covered
                          else "Outside weekly availability"})

    return {
        "staff_id": person["staff_id"],
        "name": person.get("name"),
        "role": person.get("role"),
        "specialisation": person.get("specialisation"),
        "department": person.get("department"),
        "employment_status": person.get("employment_status"),
        "availability_status": person.get("availability_status"),
        "eligible": blocked is None,
        "blocked_reason": blocked,
        "weekly_ok": covered,
        "notes": notes,
    }


def _week_label(start, end):
    """e.g. "31 Aug – 6 Sep 2026" from real calculated dates."""
    left = start.strftime("%-d %b") if start.year == end.year else start.strftime("%-d %b %Y")
    return f"{left} – {end.strftime('%-d %b %Y')}"


def _week_roster_summary(week_shifts, conflicts):
    """Derive the weekly planning summary. Nothing here is persisted.

    "Roster ready" is a calculated statement about the current data, not a
    stored publication state.
    """
    required = sum(int(row.get("required_staff_count", 0)) for row in week_shifts)
    assigned = sum(int(row.get("assigned_staff_count", 0)) for row in week_shifts)
    unfilled = sum(max(int(row.get("required_staff_count", 0))
                        - int(row.get("assigned_staff_count", 0)), 0)
                    for row in week_shifts)
    # An empty week is not a completed roster — nothing has been planned yet.
    empty = len(week_shifts) == 0
    ready = (not empty) and unfilled == 0 and not conflicts
    return {
        "shift_count": len(week_shifts),
        "required_positions": required,
        "assigned_positions": assigned,
        "unfilled_positions": unfilled,
        "conflict_count": len(conflicts),
        "empty": empty,
        "ready": ready,
        "label": "Roster ready" if ready else "Roster incomplete",
    }


def _week_conflicts(week_shifts, assignments_by_shift, staff_by_id,
                    weekly_by_staff, shifts_by_staff):
    """Deterministic conflicts across the selected week.

    Detects: overlapping assignments, assignment outside recurring weekly
    availability, and staff who are operationally Unavailable or On Leave.
    Nothing is auto-corrected — each is surfaced for the manager to decide.
    """
    conflicts = []
    for shift in week_shifts:
        for row in assignments_by_shift.get(shift["shift_id"], []):
            staff_id = row.get("staff_id")
            person = staff_by_id.get(staff_id)
            if not person:
                continue
            base = {"shift_id": shift["shift_id"], "staff_id": staff_id,
                    "staff_name": person.get("name"),
                    "department": shift.get("department"),
                    "shift_date": shift.get("shift_date"),
                    "start_time": shift.get("start_time"),
                    "end_time": shift.get("end_time")}

            status = person.get("availability_status")
            if status in ("Unavailable", "On Leave"):
                conflicts.append({**base, "kind": "status",
                                   "detail": f"Assigned while {status}"})

            if not _weekly_covers_shift(weekly_by_staff.get(staff_id, []), shift):
                conflicts.append({**base, "kind": "availability",
                                   "detail": "Assigned outside weekly availability"})

            for other in shifts_by_staff.get(staff_id, []):
                if other.get("shift_id") == shift["shift_id"]:
                    continue
                if other.get("assignment_status") in _INACTIVE_ASSIGNMENT:
                    continue
                if _shifts_overlap(other, shift) and other.get("shift_id", 0) > shift["shift_id"]:
                    conflicts.append({
                        **base, "kind": "overlap",
                        "detail": ("Overlaps " + str(other.get("department", "")) + " "
                                    + str(other.get("shift_date", "")) + " "
                                    + str(other.get("start_time", "")) + "-"
                                    + str(other.get("end_time", "")))})
    return conflicts


def create_app() -> Flask:
    # Absolute template/static paths: Flask's default resolution relies on
    # how the module was imported, which breaks when app.py is loaded via
    # importlib (as the test suite does) rather than a normal `import app`.
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
        template_folder=str(BASE_DIR / "templates"),
    )

    # Display helpers shared by the table and the drawer. Presentation only —
    # every API call still uses the real numeric primary key.
    app.jinja_env.globals["display_id"] = _display_id
    app.jinja_env.globals["shift_display_id"] = _shift_display_id
    app.jinja_env.globals["initials"] = _initials
    app.jinja_env.globals["date_long"] = _date_long
    app.jinja_env.globals["timedelta"] = datetime.timedelta

    # ---------------------------------------------------------- shared assets
    @app.get("/shared/<path:filename>")
    def shared_assets(filename: str):
        """Serve shared/frontend so this page can link /shared/css/main.css.

        Kept separate from Flask's own static folder because the shared
        design system lives outside this microservice's directory and is
        owned by the whole team, not by Student 5.
        """
        return send_from_directory(SHARED_FRONTEND_DIR, filename)

    # ------------------------------------------------------------------ health
    @app.get("/health")
    def health():
        try:
            backend = api_client.get_health()
            status = "ok"
        except (BackendUnavailableError, BackendError) as error:
            backend, status = {"message": str(error)}, "unavailable"

        return {
            "status": "ok" if status == "ok" else "degraded",
            "service": "student-5-frontend",
            "feature": "Staff & Shift Management",
            "backend_service": {"url": api_client.API_BASE_URL, "status": status,
                                "detail": backend},
        }, (200 if status == "ok" else 503)

    # -------------------------------------------------------------------- page
    @app.get("/")
    def workforce_overview():
        """Render the page shell. No backend call here — HTMX fetches the
        operational content once the shell is on screen, so the page is
        usable immediately even if the backend is down.

        kpis/shifts/summary are explicitly None (not merely absent) so the
        included partials render their loading skeleton rather than a blank
        or Undefined value on first paint.
        """
        return render_template(
            "workforce_overview.html", today=_today(), today_long=_today_long(),
            active="workforce",
            kpis=None, shifts=None, top_gap=None, summary=None, error=None,
            departments=None,
        )

    # --------------------------------------------------------- staff directory
    @app.get("/staff")
    def staff_directory():
        """Staff Directory shell. Filter options are derived from the real
        staff records the service returns — never a hard-coded list."""
        try:
            records = api_client.list_staff()["staff"]
            departments = sorted({r["department"] for r in records if r.get("department")})
            roles = sorted({r["role"] for r in records if r.get("role")})
            employments = sorted({r["employment_status"] for r in records
                                   if r.get("employment_status")})
        except (BackendUnavailableError, BackendError):
            # The shell still renders; the table partial reports the failure.
            departments, roles, employments = [], [], []

        return render_template(
            "staff_directory.html", today=_today(), active="staff",
            departments=departments, roles=roles, employments=employments,
            staff=None, error=None,
        )

    # ------------------------------------------------------------ shift planner
    @app.get("/shifts")
    def shift_planner():
        """Render the persistent planner shell; HTMX fills its workspace."""
        selected_date = request.args.get("date") or _today()
        if not _valid_iso_date(selected_date):
            selected_date = _today()

        # Deep-link context from Workforce Overview's "Manage shift" action.
        # These seed the initial #planner-state form so the planner opens on
        # the right day/department with the affected shift already selected,
        # instead of the manager having to find it again.
        deep_department = request.args.get("department") or ""
        # The planner's two views are "week" and "timeline" (the Day Timeline).
        deep_view = request.args.get("view") if request.args.get("view") in ("week", "timeline") else "week"
        deep_shift_id = request.args.get("selected_shift_id") or ""
        if deep_shift_id and not deep_shift_id.isdigit():
            deep_shift_id = ""

        service_available = True
        try:
            records = api_client.list_shifts()["shifts"]
            departments = sorted({row["department"] for row in records
                                  if row.get("department")})
            roles = sorted({row["required_role"] for row in records
                            if row.get("required_role")})
        except (BackendUnavailableError, BackendError):
            departments, roles, service_available = [], [], False

        return render_template(
            "shift_planner.html", today=_today(), active="shifts",
            selected_date=selected_date, departments=departments, roles=roles,
            statuses=api_client.SHIFT_STATUSES,
            week_start=_week_start_for(selected_date).isoformat(),
            service_available=service_available,
            deep_department=deep_department, deep_view=deep_view,
            deep_shift_id=deep_shift_id,
        )

    @app.get("/partials/planner")
    def planner_workspace_partial():
        """Render the week grid or day timeline from existing API records."""
        selected_date = request.args.get("selected_date") or _today()
        week_start = request.args.get("week_start") or selected_date
        department = request.args.get("department") or None
        required_role = request.args.get("required_role") or None
        shift_status = request.args.get("shift_status") or None
        view = request.args.get("view") or "week"
        selected_shift_id = request.args.get("selected_shift_id") or None

        try:
            # One complete server-side dataset keeps filter option values and
            # cross-department timeline context stable. Role/status filtering
            # is still performed in this Flask service, never in the browser.
            shift_data = api_client.list_shifts()
            coverage_data = api_client.get_coverage()
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/planner_workspace.html", error=str(error))

        all_records = shift_data.get("shifts", [])
        roles = sorted({row.get("required_role") for row in all_records
                        if row.get("required_role")})
        model = _build_planner_model(
            all_records, coverage_data.get("shifts", []), week_start,
            selected_date, selected_department=department,
            required_role=required_role, shift_status=shift_status,
            view=view)

        detail_html = None
        if selected_shift_id:
            try:
                detail_id = int(selected_shift_id)
            except (TypeError, ValueError):
                detail_id = None
            valid_shift_ids = {int(row["shift_id"]) for row in all_records}
            if detail_id is not None and detail_id in valid_shift_ids:
                detail_html = _render_shift_detail(detail_id, embedded=True)
            else:
                selected_shift_id = ""

        return render_template(
            "partials/planner_workspace.html", error=None,
            roles=roles, statuses=api_client.SHIFT_STATUSES,
            selected_shift_id=selected_shift_id or "", detail_html=detail_html,
            **model)

    @app.get("/partials/shifts")
    def shifts_partial():
        selected_date = request.args.get("shift_date") or _today()
        department = request.args.get("department") or None
        required_role = request.args.get("required_role") or None
        shift_status = request.args.get("shift_status") or None
        filtered = bool(department or required_role or shift_status)

        try:
            shift_data = api_client.list_shifts(
                shift_date=selected_date, department=department,
                shift_status=shift_status)
            coverage_data = api_client.get_coverage(
                shift_date=selected_date, department=department,
                shift_status=shift_status)
        except (BackendUnavailableError, BackendError) as error:
            return render_template(
                "partials/shift_list.html", shifts=None, error=str(error),
                filtered=filtered, selected_date=selected_date)

        coverage_by_id = {row["shift_id"]: row for row in coverage_data["shifts"]}
        shifts = []
        for shift in shift_data["shifts"]:
            if required_role and shift.get("required_role") != required_role:
                continue
            row = dict(shift)
            counts = coverage_by_id.get(shift["shift_id"], {})
            assigned = int(counts.get("assigned_staff_count", 0))
            required = int(shift["required_staff_count"])
            row["assigned_staff_count"] = assigned
            row["coverage"] = _coverage(assigned, required)
            shifts.append(row)

        return render_template(
            "partials/shift_list.html", shifts=shifts, error=None,
            filtered=filtered, selected_date=selected_date)

    def _render_shift_detail(shift_id, notice=None, embedded=False):
        try:
            shift = api_client.get_shift(shift_id)["shift"]
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None,
                                   embedded=embedded)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=False, error=str(error), notice=None,
                                   embedded=embedded)

        assignments, assignment_error = None, None
        try:
            records = api_client.list_shift_assignments(shift_id)["assignments"]
            assignments = [row for row in records
                           if row.get("assignment_status") not in _INACTIVE_ASSIGNMENT]
        except (BackendUnavailableError, BackendError) as error:
            assignment_error = str(error)

        candidates, candidate_error = [], None
        if assignments is None:
            candidate_error = (
                "Candidate assignment is unavailable until current assignments can be loaded.")
        else:
            try:
                # Fetch by required role only. Operational status and conflicts
                # are ANNOTATED rather than filtered out, so the manager can
                # see why someone is unavailable instead of them silently
                # vanishing from the list.
                candidate_records = api_client.list_staff(
                    role=shift["required_role"])["staff"]
                active_ids = {row["staff_id"] for row in assignments}

                evaluated = []
                for person in candidate_records:
                    staff_id = person["staff_id"]
                    try:
                        their_shifts = api_client.list_staff_shifts(staff_id)["shifts"]
                    except (BackendUnavailableError, BackendError):
                        their_shifts = []
                    try:
                        periods = api_client.get_weekly_availability(staff_id)["periods"]
                    except (BackendUnavailableError, BackendError):
                        periods = []
                    evaluated.append(_evaluate_candidate(
                        person, shift, active_ids, their_shifts, periods))

                # Eligible first, then weekly-availability match, then the
                # employment ordering aid, then name. Ordering only.
                evaluated.sort(key=lambda row: (
                    not row["eligible"],
                    not row["weekly_ok"],
                    _EMPLOYMENT_PRIORITY.get(row.get("employment_status"), 2),
                    row.get("department") != shift["department"],
                    row.get("name") or ""))
                candidates = evaluated
            except (BackendUnavailableError, BackendError) as error:
                candidate_error = str(error)

        assigned_count = len(assignments) if assignments is not None else None
        coverage = (_coverage(assigned_count, int(shift["required_staff_count"]))
                    if assigned_count is not None else None)
        return render_template(
            "partials/shift_detail.html", shift=shift, not_found=False,
            error=None, notice=notice, assignments=assignments,
            assignment_error=assignment_error, candidates=candidates,
            candidate_error=candidate_error, coverage=coverage,
            assigned_count=assigned_count, embedded=embedded,
            eligible_count=sum(1 for row in candidates if row.get("eligible")),
        )

    @app.get("/partials/shifts/<int:shift_id>")
    def shift_detail_partial(shift_id: int):
        return _render_shift_detail(shift_id, embedded=request.args.get("panel") == "1")

    def _shift_form_values(source):
        return {
            "department": (source.get("department") or "").strip(),
            "shift_date": (source.get("shift_date") or "").strip(),
            "start_time": (source.get("start_time") or "").strip(),
            "end_time": (source.get("end_time") or "").strip(),
            "required_role": (source.get("required_role") or "").strip(),
            "required_staff_count": source.get("required_staff_count", 1),
            "shift_status": (source.get("shift_status") or "Planned").strip(),
            "notes": (source.get("notes") or "").strip(),
        }

    def _shift_payload(values):
        try:
            count = int(values["required_staff_count"])
        except (TypeError, ValueError):
            raise ValueError("Required staff count must be a whole number greater than zero.")
        if count < 1:
            raise ValueError("Required staff count must be greater than zero.")
        return {**values, "required_staff_count": count,
                "notes": values["notes"] or None}

    def _render_shift_form(values, mode, shift_id=None, error=None):
        return render_template(
            "partials/shift_form.html", values=values, mode=mode,
            shift_id=shift_id, statuses=api_client.SHIFT_STATUSES, error=error)

    @app.get("/partials/roster-status")
    def roster_status_partial():
        """Weekly roster summary and deterministic conflict review.

        Everything here is derived from existing shift/assignment/availability
        data on each request — no roster state is persisted.
        """
        week_start = request.args.get("week_start") or _week_start().isoformat()
        try:
            start = datetime.datetime.strptime(week_start, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            start = _week_start()
        end = start + datetime.timedelta(days=6)

        try:
            coverage_rows = api_client.get_coverage()["shifts"]
            staff_rows = api_client.list_staff()["staff"]
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/roster_status.html",
                                    summary=None, conflicts=None, error=str(error),
                                    week_start=start.isoformat(),
                                    week_end=end.isoformat())

        week_shifts = [row for row in coverage_rows
                       if start.isoformat() <= row.get("shift_date", "") <= end.isoformat()]

        staff_by_id = {row["staff_id"]: row for row in staff_rows}
        assignments_by_shift, weekly_by_staff, shifts_by_staff = {}, {}, {}
        partial_error = None

        for shift in week_shifts:
            try:
                assignments_by_shift[shift["shift_id"]] = [
                    row for row in api_client.list_shift_assignments(
                        shift["shift_id"])["assignments"]
                    if row.get("assignment_status") not in _INACTIVE_ASSIGNMENT]
            except (BackendUnavailableError, BackendError) as error:
                assignments_by_shift[shift["shift_id"]] = []
                partial_error = str(error)

        involved = {row.get("staff_id")
                    for rows in assignments_by_shift.values() for row in rows}
        for staff_id in involved:
            try:
                weekly_by_staff[staff_id] = api_client.get_weekly_availability(
                    staff_id)["periods"]
            except (BackendUnavailableError, BackendError):
                weekly_by_staff[staff_id] = []
            try:
                shifts_by_staff[staff_id] = api_client.list_staff_shifts(
                    staff_id)["shifts"]
            except (BackendUnavailableError, BackendError):
                shifts_by_staff[staff_id] = []

        conflicts = _week_conflicts(week_shifts, assignments_by_shift,
                                     staff_by_id, weekly_by_staff, shifts_by_staff)
        summary = _week_roster_summary(week_shifts, conflicts)

        # Department roll-up for the "remaining gaps" view.
        departments = {}
        for row in week_shifts:
            entry = departments.setdefault(row["department"],
                                            {"department": row["department"], "gap": 0})
            entry["gap"] += max(int(row.get("required_staff_count", 0))
                                 - int(row.get("assigned_staff_count", 0)), 0)

        return render_template(
            "partials/roster_status.html", summary=summary, conflicts=conflicts,
            departments=sorted(departments.values(), key=lambda d: d["department"]),
            error=partial_error, week_start=start.isoformat(),
            week_end=end.isoformat(), week_label=_week_label(start, end))

    @app.get("/partials/shifts/new")
    def new_shift_partial():
        selected_date = request.args.get("shift_date") or _today()
        if not _valid_iso_date(selected_date):
            selected_date = _today()
        return _render_shift_form({
            "department": "", "shift_date": selected_date,
            "start_time": "07:00", "end_time": "15:00",
            "required_role": "", "required_staff_count": 1,
            "shift_status": "Planned", "notes": "",
        }, "create")

    @app.get("/partials/shifts/<int:shift_id>/edit")
    def edit_shift_partial(shift_id: int):
        try:
            values = _shift_form_values(api_client.get_shift(shift_id)["shift"])
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=False, error=str(error), notice=None)
        return _render_shift_form(values, "edit", shift_id=shift_id)

    @app.post("/partials/shifts")
    def create_shift_partial():
        values = _shift_form_values(request.form)
        try:
            payload = _shift_payload(values)
            created = api_client.create_shift(payload)["shift"]
        except ValueError as error:
            return _render_shift_form(values, "create", error=str(error))
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_form(values, "create", error=str(error))

        response = make_response(_render_shift_detail(
            created["shift_id"], notice={"kind": "success", "text": "Shift created."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>")
    def update_shift_partial(shift_id: int):
        values = _shift_form_values(request.form)
        try:
            api_client.update_shift(shift_id, _shift_payload(values))
        except ValueError as error:
            return _render_shift_form(values, "edit", shift_id=shift_id, error=str(error))
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_form(values, "edit", shift_id=shift_id, error=str(error))

        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Shift updated."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>/delete")
    def delete_shift_partial(shift_id: int):
        embedded = request.form.get("panel") == "1"
        try:
            api_client.delete_shift(shift_id)
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None,
                                   embedded=embedded)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Shift was not deleted. " + str(error)},
                embedded=embedded)

        body = render_template("partials/shift_action_result.html",
                               title="Shift deleted",
                               message="The shift and its assignment records were permanently deleted.")
        response = make_response(body)
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    def _staff_id_from_form():
        try:
            return int(request.form.get("staff_id", ""))
        except (TypeError, ValueError):
            return None

    @app.post("/partials/shifts/<int:shift_id>/assign")
    def assign_shift_staff_partial(shift_id: int):
        embedded = request.form.get("panel") == "1"
        staff_id = _staff_id_from_form()
        if staff_id is None:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Select a valid staff member."},
                embedded=embedded)
        try:
            api_client.assign_staff(shift_id, staff_id)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Staff was not assigned. " + str(error)},
                embedded=embedded)
        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Staff assigned to shift."},
            embedded=embedded))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>/unassign")
    def unassign_shift_staff_partial(shift_id: int):
        embedded = request.form.get("panel") == "1"
        staff_id = _staff_id_from_form()
        if staff_id is None:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Select a valid staff member."},
                embedded=embedded)
        try:
            api_client.unassign_staff(shift_id, staff_id)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Staff was not unassigned. " + str(error)},
                embedded=embedded)
        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Staff unassigned from shift."},
            embedded=embedded))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.get("/partials/staff-table")
    def staff_table_partial():
        query = (request.args.get("q") or "").strip()
        department = request.args.get("department") or None
        role = request.args.get("role") or None
        availability = request.args.get("availability_status") or None
        employment = request.args.get("employment_status") or None
        filtered = bool(query or department or role or availability or employment)

        try:
            # /api/staff/search rejects a blank q, so only use it when the
            # user actually typed something; otherwise the plain list
            # endpoint already applies the same three filters.
            if query:
                data = api_client.search_staff(
                    query=query, department=department, role=role,
                    availability_status=availability,
                    employment_status=employment)
            else:
                data = api_client.list_staff(
                    availability_status=availability, department=department,
                    role=role, employment_status=employment)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/staff_table.html",
                                    staff=None, filtered=filtered, error=str(error))

        return render_template("partials/staff_table.html",
                                staff=data["staff"], filtered=filtered, error=None)

    def _render_detail(staff_id, notice=None, status_code=200):
        """Render the drawer for one staff member.

        Shift data is fetched separately: if that call fails the staff record
        still renders, with the assignment sections marked unavailable rather
        than taking the whole drawer down.
        """
        try:
            record = api_client.get_staff(staff_id)["staff"]
        except NotFoundError:
            return render_template("partials/staff_detail.html",
                                    person=None, error=None, notice=None,
                                    not_found=True), 404
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/staff_detail.html",
                                    person=None, error=str(error), notice=None), status_code

        current, upcoming, shifts_error = None, [], None
        assignments = []
        try:
            assignments = api_client.list_staff_shifts(staff_id)["shifts"]
            current, upcoming = _split_shifts(assignments)
        except (BackendUnavailableError, BackendError) as error:
            shifts_error = str(error)

        # Weekly availability loads independently: a failure here marks only
        # that section unavailable and leaves the rest of the drawer intact.
        weekly_grid, weekly_error = None, None
        try:
            periods = api_client.get_weekly_availability(staff_id)["periods"]
            weekly_grid = _build_weekly_grid(periods, assignments, _week_start())
        except (BackendUnavailableError, BackendError) as error:
            weekly_error = str(error)

        return render_template(
            "partials/staff_detail.html", person=record, error=None,
            notice=notice, statuses=api_client.AVAILABILITY_STATUSES,
            display_id=_display_id(staff_id), initials=_initials(record["name"]),
            last_updated=_format_timestamp(record.get("updated_at")),
            current_assignment=current, upcoming_shifts=upcoming,
            shifts_error=shifts_error,
            weekly_grid=weekly_grid, weekly_error=weekly_error,
            weekly_has_conflict=any(c["state"] == "conflict"
                                     for row in (weekly_grid or [])
                                     for c in row["cells"]),
            week_start=_week_start().isoformat(),
        ), status_code

    @app.get("/partials/staff/<int:staff_id>")
    def staff_detail_partial(staff_id: int):
        """Drawer contents for one staff member: read-only reference data,
        derived assignment context, and the operational availability control."""
        return _render_detail(staff_id)

    @app.post("/partials/staff/<int:staff_id>/availability")
    def staff_availability_update(staff_id: int):
        """Apply an availability change, then re-render the drawer.

        Availability is operational scheduling state owned by HOMS. Employee
        reference attributes are never submitted here.
        """
        status = (request.form.get("availability_status") or "").strip()

        if status not in api_client.AVAILABILITY_STATUSES:
            return _render_detail(
                staff_id, status_code=400,
                notice={"kind": "danger",
                        "text": "Select a valid availability status."})

        try:
            api_client.update_availability(staff_id, status)
        except (BackendUnavailableError, BackendError) as error:
            return _render_detail(
                staff_id,
                notice={"kind": "danger",
                        "text": "Availability was not updated. " + str(error)})

        body, code = _render_detail(
            staff_id,
            notice={"kind": "success",
                    "text": "Availability updated to " + status + "."})
        # Tell the directory table to re-query so its badge matches the drawer.
        # Emitted only on success, so a failed save never implies a change.
        response = make_response(body, code)
        response.headers["HX-Trigger"] = "staff-updated"
        return response

    @app.get("/partials/staff/<int:staff_id>/weekly-availability/edit")
    def weekly_availability_editor(staff_id: int):
        """Matrix editor. Renders the stored pattern as toggleable band cells;
        roster overlay is deliberately excluded here — you edit availability,
        not assignments."""
        try:
            record = api_client.get_staff(staff_id)["staff"]
            periods = api_client.get_weekly_availability(staff_id)["periods"]
        except NotFoundError:
            return render_template("partials/staff_detail.html",
                                    person=None, error=None, notice=None,
                                    not_found=True), 404
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/weekly_availability_edit.html",
                                    person=None, grid=None, error=str(error))

        grid = _build_weekly_grid(periods, [], _week_start())
        return render_template("partials/weekly_availability_edit.html",
                                person=record, grid=grid, error=None)

    @app.post("/partials/staff/<int:staff_id>/weekly-availability")
    def weekly_availability_save(staff_id: int):
        """Persist the submitted matrix as structured weekly periods.

        Checkbox names carry "<day>-<band index>"; they are expanded back into
        real start/end times here. UI symbols are never persisted.
        """
        periods = []
        for key in request.form.getlist("slot"):
            try:
                day_raw, band_raw = key.split("-", 1)
                day, band_index = int(day_raw), int(band_raw)
                label, start, end = WEEKLY_BANDS[band_index]
            except (ValueError, IndexError):
                continue
            if 0 <= day <= 6:
                periods.append({"day_of_week": day, "start_time": start, "end_time": end})

        try:
            api_client.replace_weekly_availability(staff_id, periods)
        except NotFoundError:
            return _render_detail(staff_id)
        except (BackendUnavailableError, BackendError) as error:
            return _render_detail(
                staff_id,
                notice={"kind": "danger",
                        "text": "Weekly availability was not saved. " + str(error)})

        return _render_detail(
            staff_id,
            notice={"kind": "success", "text": "Weekly availability updated."})

    # ---------------------------------------------------------------- partials
    @app.get("/partials/kpis")
    def kpis_partial():
        today = request.args.get("date") or _today()
        try:
            coverage = api_client.get_coverage(shift_date=today)
            roster = api_client.list_staff()
            available = api_client.list_staff(availability_status="Available")
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/kpis.html", today=today,
                                    kpis=None, error=str(error))

        shifts = coverage["shifts"]
        total_required = sum(row["required_staff_count"] for row in shifts)
        total_assigned = sum(row["assigned_staff_count"] for row in shifts)
        coverage_pct = round(total_assigned / total_required * 100) if total_required else None

        kpis = {
            "coverage_pct": coverage_pct,
            "gap": coverage["summary"]["total_shortfall"],
            "roster_total": roster["count"],
            "available_count": available["count"],
            "shifts_today": coverage["summary"]["total_shifts"],
        }
        return render_template("partials/kpis.html", today=today, kpis=kpis, error=None)

    @app.get("/partials/demand")
    def demand_partial():
        today = request.args.get("date") or _today()
        try:
            coverage = api_client.get_coverage(shift_date=today)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/demand.html", today=today,
                                    shifts=None, top_gap=None, error=str(error))

        shifts = coverage["shifts"]
        gaps = [row for row in shifts if row["shortfall"] > 0]
        top_gap = max(gaps, key=lambda row: row["shortfall"]) if gaps else None

        # Per-department roll-up for today. "positions" counts required STAFF
        # POSITIONS, which is not the same as the number of shifts — both are
        # reported so neither can be mistaken for the other.
        departments = {}
        for row in shifts:
            entry = departments.setdefault(row["department"], {
                "department": row["department"], "shift_count": 0,
                "required": 0, "assigned": 0, "gap": 0})
            entry["shift_count"] += 1
            entry["required"] += int(row["required_staff_count"])
            entry["assigned"] += int(row["assigned_staff_count"])
            entry["gap"] += int(row["shortfall"])

        # Presentation ordering only: departments needing attention first.
        department_rows = sorted(departments.values(),
                                  key=lambda d: (d["gap"] == 0, d["department"]))

        return render_template("partials/demand.html", today=today,
                                shifts=shifts, top_gap=top_gap, error=None,
                                departments=department_rows)

    @app.get("/partials/summary")
    def summary_partial():
        today = request.args.get("date") or _today()
        try:
            summary = api_client.get_coverage_summary(shift_date=today)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/summary.html", today=today,
                                    summary=None, error=str(error))
        return render_template("partials/summary.html", today=today,
                                summary=summary, error=None)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("FRONTEND_PORT", DEFAULT_PORT))
    create_app().run(host="0.0.0.0", port=port, debug=False)
