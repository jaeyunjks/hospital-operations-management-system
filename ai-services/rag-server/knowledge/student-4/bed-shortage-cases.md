# Bed Shortage Cases

## When a case is opened

When no compatible bed is available, the coordinator opens a shortage case. The
case records the patient, an optional admission reference, the required care
category, an optional required ward, an urgency of Low, Medium, High or Critical
(Medium by default), a holding location and who opened it. Opening a case
requires the `bed.allocate` permission. A case status is Open, Option offered,
Resolved, Escalated or Cancelled.

## Options offered for a case

The service offers compatible options in a fixed order:

1. **Available now** - a bed free now in the required care category and ward.
2. **Pending cleaning** - a bed in a room that is being cleaned, available once
   cleaning completes.
3. **Alternative ward** - a free bed of the right category in another ward.
4. **Escalate** - escalate to the on-call operations manager, always offered as
   the last option.

## Recording the decision

The coordinator chooses an option and must record a decision reason; both are
required. A case that is already Resolved or Cancelled cannot be decided again.

Choosing a bed reserves it. It does not occupy it: allocation still requires
creating an arrangement, so no bed is ever occupied by the decision route alone.
This is the reserve, allocate, occupy sequence that the bed shortage workflow
requires.
