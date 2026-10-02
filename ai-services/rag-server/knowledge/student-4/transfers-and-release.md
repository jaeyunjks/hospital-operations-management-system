# Transfers, Release and Discharge

## Releasing a bed

Releasing records a discharge or the end of a theatre session. It requires the
`bed.release` permission. The arrangement becomes Completed, its end time is set
to the supplied time or the current time, and the bed is freed for the next
booking. An arrangement that is already Completed or Cancelled cannot be
released again.

## Transferring a patient between beds

A transfer moves a patient to a different bed and is a routine task for the Bed
& Room Coordinator. Only an arrangement that is In Progress can be transferred,
and the target bed must be different from the current one.

The transfer completes the current arrangement at the moment of the move and
opens a new arrangement on the target bed. The new arrangement is linked to the
old one by a transferred-from reference, so the move stays traceable as a chain
rather than as two unrelated rows. The target bed passes the same conflict check
as any other booking, and the new arrangement takes the care category of the
target bed's room type.

## Cancelling

Cancelling is the soft delete for a booking that has not started. The
arrangement's status becomes Cancelled and an optional reason is recorded; the
row is never removed. An arrangement that is already Completed or Cancelled
cannot be cancelled again.
