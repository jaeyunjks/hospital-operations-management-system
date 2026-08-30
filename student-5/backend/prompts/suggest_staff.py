"""Prompt artefact: rank eligible staff for a shift.

Cross-reference: ``docs/prompts/student-5/ai-integration.md`` (``S5-AI-001``).

WHAT THE MODEL IS AND IS NOT ASKED TO DO
----------------------------------------
The model receives the eligible shortlist plus compact, structured evidence
about the coverage gap and candidates who were blocked by deterministic rules.
Its job is to order the eligible list using that evidence. The backend composes
the displayed constraint and trade-offs from the same verified fields; model
prose is never rendered. It cannot promote a blocked candidate because the
backend validates every returned id.

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
* **Displayed reasoning is fact-composed.** The model chooses order only; the
  application explains each option from verified context, preventing a fluent
  rationale from inventing a conflict or policy.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

#: The top-level key the model is told to return.
RANKING_KEY = "ranking"

SYSTEM_PROMPT = """\
You are a rostering assistant for a hospital shift planner.

You are given compact structured facts about a shift, its real coverage gap, a
short primary candidate list, other eligible alternatives, and staff who are
ineligible with the recorded reason. Your task is to order only the primary
eligible_candidates list. The context already contains the supported assessment
shown to the manager; do not rewrite it or generate free-form explanations.

Rules you must follow:
1. Only use facts present in the supplied context. Never invent missing
   operational data, staff names, assignments, conflicts, policies or causes.
2. Use ONLY staff_id values in eligible_candidates. Never invent a staff_id and
   never place an eligible_alternative or ineligible candidate in the ranking.
3. Include every eligible candidate you are given, exactly once. Do not drop anyone and
   do not repeat anyone.
4. Do not decide whether someone is available, on leave, or allowed to work.
   That has already been decided and is not your judgement to make.
5. A department mismatch is context, not a blocker. Recurring weekly
   availability is advisory; only a supplied blocking_reason is a blocker.
6. Rostered weekly hours are descriptive only. If no weekly-hours policy is
   configured, do not claim a limit, breach, overtime risk or safe threshold.
7. If the shift is already covered, do not manufacture a problem.
8. Do not assign anyone to the shift. A human staff manager makes the final
   choice; you are only ordering a list for them to consider.
9. Reply with JSON only. No prose before or after it.
"""

_RANKING_GUIDANCE = """\
Order only eligible_candidates using these factors, most important first:
1. Candidates whose department matches the shift's department.
2. Candidates whose recurring weekly availability matches the shift
   (weekly_availability_matches: true). This is a preference only, NOT a
   requirement — a candidate with false is still fully eligible and may rank
   highly for other reasons.
3. Existing assignments and real weekly_rostered_hours, without inferring any
   unstated maximum or employment-policy rule.
4. Relevant specialisation as context only; the shift has no required
   specialisation field.
5. Permanent staff (Full-Time, Part-Time) ahead of Casual or Contract cover,
   all else being equal.

Return staff_id values only. The application composes each displayed trade-off
from verified department, availability, assignment and weekly-hours fields.\
"""

_OUTPUT_CONTRACT = """\
Reply with a JSON object in exactly this shape:

{"ranking": [{"staff_id": <integer>}]}\
"""


def build_prompt(facts: Dict[str, Any],
                 candidates: Optional[List[Dict[str, Any]]] = None) -> str:
    """Assemble the user prompt from already-projected structured facts.

    ``candidates`` is accepted for compatibility with earlier prompt tests and
    is folded into the structured context. This function does no filtering of
    its own and will serialise whatever it is handed. Keeping
    the projection in ``ai_service`` means there is one place to audit what
    leaves the service, rather than a second, quieter one in here.
    """
    if candidates is not None:
        facts = {"shift": facts, "eligible_candidates": candidates}
    return "\n\n".join([
        "Grounded staffing context:",
        json.dumps(facts, indent=2, sort_keys=True),
        _RANKING_GUIDANCE,
        _OUTPUT_CONTRACT,
    ])
