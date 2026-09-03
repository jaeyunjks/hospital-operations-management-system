"""AI-assisted room suggestion and occupancy summary.

Plan -> Act -> Observe -> Adapt, as implemented by this feature:

    Plan     classify the request into a care category and build the
             candidate set of free beds within that category
    Act      send the candidates to Ollama, which ranks them and gives a
             reason for each
    Observe  validate the model's reply against the real candidate list;
             record whether the coordinator accepted or overrode it
    Adapt    every later suggestion is recomputed against current
             occupancy, so the next answer reflects the ward as it is now

The AI ranks; it never allocates. Architecture v2.2 section 5.2:
"AI never assigns a bed; an authorised employee approves the allocation."
"""

import config
from services import database_client as dbc
from services import ollama_client

# Keyword rules used to classify a request without the model, and as the
# fallback when the model is unavailable.
CATEGORY_KEYWORDS = {
    "Surgical": ["surgery", "surgical", "theatre", "operation", "operative",
                 "procedure", "anaesthetic", "post-op", "recovery"],
    "Short-term": ["emergency", "observation", "icu", "intensive", "critical",
                   "ventilator", "monitoring", "isolation", "infection",
                   "step-down", "high dependency", "acute"],
    "Long-term": ["rehabilitation", "rehab", "extended", "long stay", "long-term",
                  "recovery programme", "stable", "private room", "shared ward"],
}

SYSTEM_PROMPT = (
    "You are a hospital bed placement assistant. You rank candidate beds "
    "for a bed coordinator to review. You never assign a bed yourself. "
    "Reply with JSON only, no explanation outside the JSON."
)


def classify_category(requirements):
    """Choose a care category from free-text requirements.

    Deterministic keyword scoring. Classification decides which beds a
    patient may be placed in, so it must be reproducible and explainable
    rather than left to a small model's judgement.
    """
    text = (requirements or "").lower()
    scores = {
        category: sum(1 for word in words if word in text)
        for category, words in CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "Short-term", "No category keywords matched; defaulted to Short-term"
    matched = [w for w in CATEGORY_KEYWORDS[best] if w in text]
    return best, "Matched keyword(s): " + ", ".join(matched)


def candidate_beds(care_category, ward=None):
    """Free beds in the requested category, in a usable room."""
    rows = dbc.availability(care_category=care_category, bed_status="available", ward=ward)
    return [r for r in rows if r["room_status"] in ("Available", "In Use")]


def _fallback_ranking(candidates, reason):
    """Deterministic ranking used whenever the model cannot be trusted.

    Monitored beds first for categories that need them, then smaller
    rooms (more privacy, less disruption), then room number for a
    stable, reproducible order.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (-c["requires_monitoring"], c["default_capacity"], c["room_number"]),
    )
    return [
        {
            "bed_id": c["bed_id"],
            "bed_number": c["bed_number"],
            "room_number": c["room_number"],
            "ward": c["ward"],
            "type_name": c["type_name"],
            "reason": "Rule-based ordering ({}): {} in {}, capacity {}".format(
                reason, c["type_name"], c["ward"], c["default_capacity"]),
        }
        for c in ordered
    ]


def suggest_rooms(requirements, ward=None, limit=3):
    """Return ranked bed suggestions plus a record of how they were made."""
    category, category_reason = classify_category(requirements)
    candidates = candidate_beds(category, ward)

    plan = {
        "care_category": category,
        "classification_reason": category_reason,
        "ward_filter": ward,
        "candidate_count": len(candidates),
    }

    if not candidates:
        return {
            "plan": plan,
            "suggestions": [],
            "source": "none",
            "advisory": True,
            "message": (
                "No available bed in the {} category. Open a shortage case to record "
                "the requirement and review alternatives.".format(category)
            ),
        }

    listing = "\n".join(
        "- bed_id {}: {} in room {} ({}, {}), capacity {}, monitored: {}".format(
            c["bed_id"], c["bed_number"], c["room_number"], c["type_name"],
            c["ward"], c["default_capacity"], "yes" if c["requires_monitoring"] else "no")
        for c in candidates
    )
    prompt = (
        "A patient needs a bed.\n"
        "Patient requirements: {}\n"
        "Care category already determined: {}\n\n"
        "Available beds:\n{}\n\n"
        "Rank the {} most suitable beds. Reply with JSON only, in exactly this shape:\n"
        '[{{"bed_id": 12, "reason": "one short sentence"}}]'
    ).format(requirements, category, listing, min(limit, len(candidates)))

    valid_ids = {c["bed_id"]: c for c in candidates}

    try:
        parsed, attempts = ollama_client.generate_json(prompt, system=SYSTEM_PROMPT)
    except ollama_client.AIUnavailable as error:
        # The raw exception is useful in a log and in the API response, but a
        # connection stack trace has no place on a coordinator's screen.
        return {
            "plan": plan,
            "suggestions": _fallback_ranking(candidates, "AI unavailable")[:limit],
            "source": "fallback",
            "advisory": True,
            "message": "The AI model is unavailable, so beds are ordered by rule instead.",
            "detail": str(error),
        }

    # Observe: keep only beds the model was actually offered.
    ranked, rejected = [], []
    for item in parsed if isinstance(parsed, list) else []:
        bed_id = item.get("bed_id")
        if bed_id in valid_ids and not any(r["bed_id"] == bed_id for r in ranked):
            bed = valid_ids[bed_id]
            ranked.append({
                "bed_id": bed_id,
                "bed_number": bed["bed_number"],
                "room_number": bed["room_number"],
                "ward": bed["ward"],
                "type_name": bed["type_name"],
                "reason": str(item.get("reason", ""))[:300],
            })
        else:
            rejected.append(bed_id)

    if not ranked:
        return {
            "plan": plan,
            "suggestions": _fallback_ranking(candidates, "AI output rejected")[:limit],
            "source": "fallback",
            "advisory": True,
            "message": "The model suggested beds that were not available; "
                       "showing rule-based ordering instead.",
        }

    return {
        "plan": plan,
        "suggestions": ranked[:limit],
        "source": "ai",
        "model": config.OLLAMA_MODEL,
        "attempts": attempts,
        "discarded_ids": rejected,
        "advisory": True,
        "message": "AI recommendation for coordinator review. "
                   "An authorised employee must approve the allocation.",
    }


def occupancy_summary():
    """Plain-language summary of ward occupancy and theatre utilisation."""
    stats = dbc.occupancy_stats()

    prompt = (
        "Summarise this hospital occupancy snapshot for a bed coordinator in "
        "three short sentences. State which care categories are under pressure "
        "and how many operating theatres are usable. Do not invent numbers.\n\n"
        + str(stats)
    )

    try:
        text = ollama_client.generate(prompt, temperature=0.3)
        source = "ai"
    except ollama_client.AIUnavailable as error:
        return {
            "summary": _fallback_summary(stats),
            "stats": stats,
            "source": "fallback",
            "advisory": True,
            "message": "The AI model is unavailable; these figures are counted directly "
                       "from the database.",
            "detail": str(error),
        }

    return {"summary": text, "stats": stats, "source": source,
            "model": config.OLLAMA_MODEL, "advisory": True}


def _fallback_summary(stats):
    """Deterministic summary so the panel is never blank."""
    parts = []
    for row in stats.get("by_care_category", []):
        parts.append("{}: {} of {} beds available".format(
            row["care_category"], row["available"], row["total_beds"]))
    theatres = {row["status"]: row["rooms"] for row in stats.get("theatres", [])}
    parts.append("Operating theatres — " + ", ".join(
        "{} {}".format(count, status.lower()) for status, count in theatres.items()
    ) if theatres else "No operating theatres configured")
    open_cases = sum(row["cases"] for row in stats.get("open_shortages", []))
    parts.append("{} unresolved shortage case(s)".format(open_cases))
    return ". ".join(parts) + "."
