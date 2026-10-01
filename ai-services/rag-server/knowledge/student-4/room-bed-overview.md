# Room and Bed Management Overview

## What this feature covers

Room & Bed Management owns hospital rooms, beds, operating theatres and the
arrangements placed on them. It tracks room state, bed allocation, occupancy,
bed shortages and release. Its primary user is the Bed & Room Coordinator; the
Hospital Operations Manager and the Admissions Coordinator have read or limited
access to the same data.

The feature runs as three services: a frontend on port 3400, a backend API on
port 5400 and a database service on port 6400. The backend never opens the
SQLite file directly; it calls the database service over HTTP, and no other
team service reads that database. Cross-service access is by API only.

## Rooms, beds and room types

Every room belongs to a room type, and each room type has a care category:
Surgical, Short-term or Long-term. A room type also records a default capacity,
whether the room requires monitoring, and a description. Grouping rooms this way
keeps patients with similar care needs together and narrows the candidates the
suggestion endpoint ranks.

A room has a room number, a ward, a floor, a type and a status: Available,
In Use, Cleaning or Out of Service. A bed is any allocatable slot inside a room,
including an operating table, and has its own status: available, reserved,
occupied or maintenance.

## Soft delete and audit trail

Nothing is deleted. A room that is withdrawn from use becomes Out of Service and
a bed that is withdrawn becomes maintenance, so historical arrangements stay
valid. Arrangements are cancelled rather than removed, which preserves a
complete audit trail.

## Permissions

Permissions come from the shared Authentication and RBAC service, not from this
feature. A Receptionist or bed coordinator holds `bed.allocate` and
`bed.release`. A System Administrator holds `room.configure`. Doctors, nurses and
specialists have read-only access.
