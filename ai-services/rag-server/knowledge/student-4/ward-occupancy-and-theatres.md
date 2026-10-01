# Ward Occupancy and the Theatre Board

## Ward occupancy

Ward occupancy is published as read-only context for other services, for example
Staff & Shift Management, which uses it for staffing context. For each ward it
reports total beds, occupied, available, reserved, maintenance, monitored beds,
an occupancy percentage and the care categories present, plus the same totals
across all wards. One ward can be requested by name.

The endpoint deliberately contains no AI involvement, so another service can
depend on the numbers without depending on a model being available. The figures
are the current recorded snapshot. They are not a forecast, a staffing
requirement, a nurse ratio or a clinical risk score, and monitored beds counts
monitoring-capable slots rather than patient acuity.

## Sharing occupancy through MCP

The shared MCP server exposes ward occupancy as the read-only tool
`homs_ward_occupancy_status`, which calls this published API. The tool returns
only ward-level counts: no patient, admission, arrangement, room or bed
identifiers, names, procedures or notes. A ward name must match this feature's
ward names exactly; the tool does not translate another feature's department
names.

## The theatre board

The theatre board answers which theatres are in use, free or unusable right now,
and what is running or scheduled next in each. A theatre's room status is shown
as Free, In use, Being cleaned or Unusable. Theatre sessions are ordinary
arrangements with the purpose Surgery, so they obey the same double-booking rule
as beds.
