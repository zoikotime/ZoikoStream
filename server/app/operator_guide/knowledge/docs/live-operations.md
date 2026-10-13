---
id: live-operations
title: Live operations
version: "2026.10"
path: /host/dashboard
---

## Broadcast sessions
A broadcast session is preview, live, paused or ended. Going live and ending the broadcast are done only in the host console's dedicated controls. Guide can show readiness and health, and open the console, but never starts or ends a broadcast.

## Program health
The platform monitors each live session and reports a health level: OK (stable), warn (degraded) or down. Health issues name what was detected.

## Live inputs (ingest)
Encoders connect through RTMP or WHIP live inputs. An input is inactive, buffering, publishing, in error or complete. Its governed signal state is healthy, intermittent or interrupted; interruptions are counted. Stream keys are shown only in the live input settings, never in Guide.

## Contributors
Remote contributors move through waiting, connected, ready, on standby, live, muted, reconnecting, removed or failed.

## Audience telemetry
The platform samples the audience every 15 seconds: viewers, participants, people on stage, messages, questions, reactions and raised hands. A sample older than about a minute is not current.

## Critical controls
Start broadcast, end broadcast, forcing recovery output, disabling hard access controls, overriding a hard gate and deleting media are controlled operations. They stay in their dedicated controls and are never executed from a Guide conversation.
