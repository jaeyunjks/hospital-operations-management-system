# Bed Allocation and Double-Booking Prevention

## What an arrangement is

An arrangement is a booking placed on one bed. Its purpose is either
"Inpatient stay" or "Surgery", so inpatient stays and operating theatre sessions
are held in the same place and the conflict check covers both. An arrangement
records the bed, the patient, an optional admission reference, a care category,
a start time, an optional end time, who arranged it, and its status: Scheduled,
In Progress, Completed or Cancelled.

Creating an arrangement requires the `bed.allocate` permission. A Surgery
arrangement must also supply a procedure name and a surgeon name.

## Where the care category comes from

The care category of an arrangement is always taken from the room type of the
bed being booked, never from the caller. An arrangement therefore cannot claim a
care category that the bed does not actually provide.

## The double-booking rule

A bed, including an operating table, can hold only one active arrangement at any
instant. The same bed can never hold two bookings at the same time, and every
create, reschedule and transfer passes the conflict check before anything is
written. Two bookings clash when each starts before the other ends. An
open-ended stay, one with no end time, is treated as running indefinitely, so an
occupied inpatient bed blocks every later booking until it is released.

A booking is also refused when the bed is under maintenance, or when the room is
Out of Service. A refused booking returns HTTP 409 with the clashing
arrangement's identifier and time window.

## How bed and room status follow an arrangement

Bed status follows the arrangement placed on it: Scheduled makes a free bed
reserved, In Progress makes it occupied, and Completed or Cancelled returns it to
reserved if another active arrangement remains, or to available if none does. A
bed under maintenance is never changed automatically.

Room status is then recomputed from its beds: a room with any occupied bed is
In Use, otherwise it is Available. A room that is Cleaning or Out of Service
keeps that status, because those are operational decisions a coordinator made and
occupancy must not silently overwrite them.

## Changing an arrangement

An arrangement that is Completed or Cancelled cannot be modified. Rescheduling
re-runs the conflict check, excluding the arrangement itself. An arrangement that
is In Progress cannot be cancelled; it is released instead.
