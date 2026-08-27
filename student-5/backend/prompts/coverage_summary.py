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
* **No recommended actions.** The summary describes a position; a manager
  decides what to do about it. Nothing in this feature can act on a shift.
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

SYSTEM_PROMPT = """\
You are a staffing assistant for a hospital shift planner.

You are given staffing coverage figures that the hospital's own system has
already calculated. Your only task is to describe what they show, briefly, and
say which shortage matters most.

Rules you must follow:
1. Use ONLY the numbers you are given. Never calculate a new number, never
   estimate one, and never state a figure that does not appear in the data
   above. If you are unsure of a number, describe the situation in words
   instead.
2. Never say how many staff a shift or department SHOULD have. The required
   numbers are already decided and are given to you.
3. Never mention patients, occupancy, bed numbers, acuity or how busy anywhere
   is. You have not been given that information and it does not exist here.
4. Never state clinical or regulatory staffing rules, ratios or standards.
5. Do not recommend actions, reassignments or automatic fixes. A human staff
   manager decides what to do; you only describe the position.
6. Reply with JSON only. No prose before or after it.
"""

_NARRATION_GUIDANCE = """\
Write a short operational summary covering, where the data supports it:
1. The overall staffing position.
2. Where the gaps are — which departments, roles or times are short.
3. Any shifts with more staff than required, if that is worth noting.
4. Areas that are fully staffed, if that is useful reassurance.

Then list the highest-priority issues, most urgent first. A shift with nobody
assigned matters more than one that is short by a single person. If nothing is
short, return an empty list rather than inventing a concern.\
"""

_OUTPUT_CONTRACT = """\
Reply with a JSON object in exactly this shape:

{"summary": "<2-4 sentences>", "priorities": ["<short phrase>", "..."]}\
"""


def build_prompt(facts: Dict[str, Any]) -> str:
    """Assemble the user prompt from already-projected coverage facts.

    The argument must already be minimised by the caller — this function does
    no filtering of its own and will serialise whatever it is handed. Keeping
    the projection in ``ai_service`` means there is one place to audit what
    leaves the service, rather than a second, quieter one in here.
    """
    return "\n\n".join([
        "Staffing coverage figures:",
        json.dumps(facts, indent=2, sort_keys=True),
        _NARRATION_GUIDANCE,
        _OUTPUT_CONTRACT,
    ])
