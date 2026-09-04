"""Input validation helpers shared by the route modules."""

from responses import ApiError


def require_fields(payload, *names):
    if not isinstance(payload, dict):
        raise ApiError("Request body must be a JSON object")
    missing = [n for n in names if payload.get(n) in (None, "")]
    if missing:
        raise ApiError("Missing required field(s): " + ", ".join(missing))


def check_choice(value, allowed, field):
    if value not in allowed:
        raise ApiError("{} must be one of: {}".format(field, ", ".join(allowed)))
    return value


def check_time_window(start, end):
    """End must be after start when both are supplied."""
    if end and start and end <= start:
        raise ApiError("end_time must be later than start_time")
