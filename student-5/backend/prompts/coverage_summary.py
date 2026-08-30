"""Prompt artefact: narrate a staffing coverage position.

Cross-reference: ``docs/prompts/student-5/ai-integration.md`` (``S5-AI-001``).

WHAT THE MODEL IS AND IS NOT ASKED TO DO
----------------------------------------
The model receives coverage facts that have ALREADY been calculated by
``coverage_service``, which is the single source of truth for every staffing
number in this system. Its only job is to say, in a few sentences, what those
figures mean operationally and which shortage a manager should look at first.

It is not asked to work anything out. It cannot decide how many staff a shift
needs, how busy a ward is, or what a safe staffing level looks like — none of
that is in the prompt, and none of it is its to judge. The instructions below
say so in words; ``ai_service`` then rejects any narrative containing a number
the facts do not support and falls back to the deterministic summary. The
prompt is the polite request; the validation is the rule.

DESIGN NOTES
------------
* **Numbers are borrowed, never produced.** The model is told to use only the
  figures it was given. A summary that invents "three nurses short" is worse
  than no summary at all, because it reads exactly like the real thing.
* **No occupancy or demand.** Patient numbers, acuity and bed occupancy are not
  modelled anywhere in Release 0. A narrative that referred to them would be
  describing data this service does not have.
* **No clinical staffing rules.** "An ICU needs one nurse per bed" is a
  clinical governance statement, not something a rostering assistant may
  assert. Required counts come from the roster, full stop.
* **Manager action, never automatic action.** The summary may identify a
  concrete review or assignment action supported by the evidence, but it
  cannot change the roster and must not fabricate a candidate.
* **Strict JSON, one shape.** The runtime is also given ``format: "json"``, so
  the instruction and the transport agree.
* **Short by contract.** A few sentences and a handful of priorities. Length
  reads as authority, and this narrative has none to claim.
"""

from __future__ import annotations

import json
from typing import Any, Dict

#: Top-level keys the model is told to return.
SUMMARY_KEY = "summary"
PRIORITIES_KEY = "priorities"
CONSTRAINT_KEY = "constraint"
NEXT_ACTION_KEY = "next_action"

SYSTEM_PROMPT = """\
You are a staffing assistant for a hospital shift planner.

You are given staffing coverage figures plus compact eligibility and blocker
facts that the hospital's own system has already calculated. Your task is to
identify the issue that deserves attention first, explain the supported
constraint, and state the next manager action when the data supports one.

Rules you must follow:
1. Only use facts present in the supplied context. Never invent staff names,
   shifts, assignments, conflicts, shortages, causes, operational data or
   policy rules.
2. Use ONLY the numbers you are given. Never calculate a new number, never
   estimate one, and never state a figure that does not appear in the data
   above. If you are unsure of a number, describe the situation in words
   instead.
3. Never say how many staff a shift or department SHOULD have. The required
   numbers are already decided and are given to you.
4. Never mention patients, occupancy, bed numbers, acuity or how busy anywhere
   is. You have not been given that information and it does not exist here.
5. Never state clinical, regulatory or employment staffing rules, ratios,
   weekly-hour limits or standards unless a configured value is supplied.
6. Department mismatch and recurring weekly availability are advisory context,
   not blockers. Only a supplied blocking_reason is a blocking fact.
7. If no eligible candidate is supplied, say so. If the data does not establish
   why a shift remains unfilled, state that uncertainty explicitly.
8. You may recommend a concrete MANAGER review or assignment action only when
   it follows from the supplied evidence. Never claim the action has happened
   and never automatically assign or unassign anyone.
9. If no shift is short, do not manufacture a problem: give brief reassurance,
   return no priorities, and do not recommend intervention.
10. Reply with JSON only. No prose before or after it.
"""

_NARRATION_GUIDANCE = """\
Write a short operational interpretation, not a duplicate KPI report:
1. Identify the issue that deserves attention first and why.
2. Use gap_analysis to explain the actual recorded blocker or confirm that an
   eligible option exists. Do not infer a cause from a coverage total alone.
3. Compare realistic options using availability, department context, current
   assignments, weekly rostered hours and employment status only when present.
4. State the concrete next manager action supported by the evidence.
5. Do not repeat totals unless a value is necessary evidence for the reasoning.
6. When no weekly-hours policy is configured, do not infer a workload limit;
   say the employment-status risk cannot be evaluated if that matters.

Then list only actionable priority issues, most urgent first. A shift with
nobody assigned matters more than one with partial cover. If nothing is short,
return an empty list rather than inventing a concern.\
"""

_OUTPUT_CONTRACT = """\
Reply with a JSON object in exactly this shape:

{"summary": "<brief operational interpretation>", "constraint": "<recorded blocker or explicit uncertainty, or null when fully covered>", "next_action": "<concrete manager action supported by the evidence, or null when fully covered>", "priorities": ["<short phrase>", "..."]}\
"""

_GAP_OUTPUT_REQUIREMENT = """\
This context contains a real staffing shortfall. The constraint and next_action
values MUST each be a non-empty JSON string. Do not return an object or array
for either field. Copy constraint EXACTLY from primary_issue.constraint and
copy next_action EXACTLY from primary_issue.manager_action. Do not paraphrase
either value. Keep blocker explanations out of summary and priorities; the
validated constraint field is the only place they belong.\
"""

_COVERED_OUTPUT_REQUIREMENT = """\
This context contains no staffing shortfall. Set constraint and next_action to
null, return priorities as an empty list, and do not manufacture an issue.\
"""


def build_prompt(facts: Dict[str, Any]) -> str:
    """Assemble the user prompt from already-projected coverage facts.

    The argument must already be minimised by the caller — this function does
    no filtering of its own and will serialise whatever it is handed. Keeping
    the projection in ``ai_service`` means there is one place to audit what
    leaves the service, rather than a second, quieter one in here.
    """
    has_gap = facts.get("totals", {}).get("total_shortfall", 0) > 0
    return "\n\n".join([
        "Staffing coverage figures:",
        json.dumps(facts, indent=2, sort_keys=True),
        _NARRATION_GUIDANCE,
        _GAP_OUTPUT_REQUIREMENT if has_gap else _COVERED_OUTPUT_REQUIREMENT,
        _OUTPUT_CONTRACT,
    ])
