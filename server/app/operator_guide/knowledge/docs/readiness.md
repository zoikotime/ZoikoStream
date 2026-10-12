---
id: readiness
title: Event readiness
version: "2026.10"
path: /organization/events
---

## How readiness is decided
Readiness is computed by the platform's readiness service from the event's real configuration. Guide reports and explains it, but cannot change it: only the readiness service moves an event to Ready.

## Readiness gates
- Title set.
- Start time scheduled.
- Host assigned.
- Recording enabled.
- Account in good standing (the organization is not suspended).
- No unresolved single-path override (a redundant contribution path exists or the risk was accepted and resolved).
- Commercial and risk-tier readiness, for events with a service profile, a risk tier above r0 or an expected audience.

## Which gates are required
- Standard impact events: gates are recommended, not required.
- High impact events: title, schedule, host and account are required.
- Unrepeatable events: every gate is required, including recording and redundancy.

## Verdicts
- Passed (Ready) — every gate passes.
- Conditional — only recommended gates fail; the event can proceed but has open risk.
- Blocked — at least one required gate fails.
The organization overview also shows Not started (no readiness work done yet) and In progress (some work gates done).

## Commercial readiness
Events with commercial obligations are also checked for required readiness checks, contributor consent and preflight, reserved capacity, the published service profile, technical readiness and, for accepted orders, financial readiness. A blocked commercial check stops the event from being armed or going live.

## Contributor preflight
Remote contributors run a technical check of browser support, camera and microphone. Required checks are browser supported, camera working and microphone working.
