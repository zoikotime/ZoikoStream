---
id: event-lifecycle
title: Event lifecycle and statuses
version: "2026.10"
path: /organization/events
---

## Event statuses
An event moves through these statuses: draft, published, scheduled, ready to arm, armed, live, degraded, ended, cancelled and archived.
- Draft — being prepared; not visible to an audience.
- Published — visible on its event page; viewers can register where registration applies.
- Scheduled — has a confirmed start time.
- Ready to arm and Armed — the event has passed the go-live gate and is waiting to start.
- Live — the broadcast is running.
- Degraded — the broadcast is running but the platform has detected a problem.
- Ended — the broadcast has finished.
- Cancelled — the event will not take place.
- Archived — removed from active lists; it can be unarchived to its previous status.

## Statuses only the platform sets
Live, degraded and ended are set by the platform from the actual broadcast, not by editing the event. Moving an event into a production state (armed, live or degraded) passes the same commercial readiness gate every time.

## Event impact
Each event has an impact level: standard, high or unrepeatable. Impact decides which readiness gates are required. Unrepeatable events (for example a memorial or a one-time ceremony) require every gate, including recording and a redundant contribution path.

## Editing events
Organization admins can edit every event in the organization. A host can edit only events they created or are assigned to host. Deleting an event is a destructive operation and is not available through Guide.

## Schedule overrun
The event's schedule state is one of on time, ending soon, ended schedule, overtime or no schedule. Overtime means the event is still running after its scheduled end time; the broadcast is not stopped automatically.
