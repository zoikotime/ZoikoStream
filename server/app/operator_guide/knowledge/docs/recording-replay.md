---
id: recording-replay
title: Recording and replay
version: "2026.10"
path: /organization/recordings
---

## Recording statuses
A recording is idle, recording, paused, stopped or failed. When recording is enabled for an event it starts with the broadcast. A failed recording records the reason.

## Dual recording and integrity
Events can capture a primary and a secondary recording. Each recording is validated: captured, validating, valid, degraded or failed. A valid recording has passed integrity checks.

## Replay publication
A recording existing never means a replay is authorized. Replay publication has its own state: not available, validating, ready for review, published, withheld, expired, or deleted (preserved). Only a published replay is available to its audience. Downloads are a separate permission.

## Retention and legal hold
Each recording has a retention policy and an expiry date. Retention can be extended through an approved request. A legal hold prevents deletion until it is released. Deleting preserved media is a destructive operation and is never available through Guide.

## Exports and reports
Organization admins can send a recording export or a post-event report to a named recipient through a delivery link that expires and can be revoked.
