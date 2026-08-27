"""Prompt artefact: rank eligible staff for a shift.

Cross-reference: ``docs/prompts/student-5/ai-integration.md`` (``S5-AI-001``).

WHAT THE MODEL IS AND IS NOT ASKED TO DO
----------------------------------------
The model receives a list that has ALREADY passed every deterministic hard rule
in ``eligibility_service``. Its only job is to order that list and say why, in
one short phrase per candidate.

It is not asked to judge availability, and it is not able to act on one: the
data it would need to overturn an eligibility decision is not in the prompt.
This is enforced twice over — the instructions below say so in words, and
``ai_service`` discards any id that was not in the input set regardless of what
the model claims. The prompt is the polite request; the validation is the rule.

DESIGN NOTES
------------
* **Identity is withheld.** Candidates are ``staff_id`` only. A name invites the
  model to reason from it, and a ranking that moves because of what someone is
  called is a fairness problem, not a rostering one.
* **Strict JSON, one shape.** The runtime is also given ``format: "json"``, so
  the instruction and the transport agree. A single top-level key keeps parsing
  unambiguous.
* **Ranking factors are stated explicitly and in order**, so the output is
  explainable to a manager rather than being an opaque preference.
* **Weekly availability is described as advisory** in the prompt itself. If it
  were presented as a rule the model would treat a mismatch as disqualifying,
  and quietly reproduce a hard rule the system deliberately does not have.
* **The rationale is capped at a short phrase.** A long one reads as authority
  the recommendation does not have.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

#: The single top-level key the model is told to return.
RANKING_KEY = "ranking"

SYSTEM_PROMPT = """\
You are a rostering assistant for a hospital shift planner.

You are given a shift and a list of staff who have ALREADY been confirmed
eligible for it by the hospital's own scheduling rules. Every candidate you are
shown can legally and practically work this shift.

Your only task is to put the candidates in a sensible order and give a short
reason for each.

Rules you must follow:
1. Use ONLY the staff_id values given to you. Never invent a staff_id.
2. Include every candidate you are given, exactly once. Do not drop anyone and
   do not repeat anyone.
3. Do not decide whether someone is available, on leave, or allowed to work.
   That has already been decided and is not your judgement to make.
4. Do not assign anyone to the shift. A human staff manager makes the final
   choice; you are only ordering a list for them to consider.
5. Reply with JSON only. No prose before or after it.
"""

_RANKING_GUIDANCE = """\
Order the candidates using these factors, most important first:
1. Candidates whose department matches the shift's department, as they already
   know the ward.
2. Candidates whose specialisation suits the shift's required role.
3. Candidates whose recurring weekly availability matches the shift
   (weekly_availability_matches: true). This is a preference only, NOT a
   requirement — a candidate with false is still fully eligible and may rank
   highly for other reasons.
4. Permanent staff (Full-Time, Part-Time) ahead of Casual or Contract cover,
   all else being equal.

Write each rationale as one short phrase of at most 12 words, describing why
that candidate suits this shift. Do not mention eligibility, availability
rules, or leave.\
"""

_OUTPUT_CONTRACT = """\
Reply with a JSON object in exactly this shape:

{"ranking": [{"staff_id": <integer>, "rationale": "<short phrase>"}]}\
"""


def build_prompt(shift: Dict[str, Any], candidates: List[Dict[str, Any]]) -> str:
    """Assemble the user prompt from an already-projected shift and candidates.

    Both arguments must already be minimised by the caller — this function does
    no filtering of its own and will serialise whatever it is handed. Keeping
    the projection in ``ai_service`` means there is one place to audit what
    leaves the service, rather than a second, quieter one in here.
    """
    return "\n\n".join([
        "Shift to fill:",
        json.dumps(shift, indent=2, sort_keys=True),
        f"Eligible candidates ({len(candidates)}):",
        json.dumps(candidates, indent=2, sort_keys=True),
        _RANKING_GUIDANCE,
        _OUTPUT_CONTRACT,
    ])
