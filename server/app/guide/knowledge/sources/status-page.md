---
source_id: status-page
title: ZoikoStream Status Page
source_type: status
canonical_path: "/status"
risk_class: MATERIAL
authority_rank: 90
version: "2026.10"
origin_files: [client/src/pages/Status.jsx]
author: guide-engineering
owner_role: support
# RBAC §5-8 approval evidence. Not yet recorded: production ingest refuses this
# source until a designated approver signs off (see knowledge/governance.py).
approved_by: pending-approval
approved_at: null
---

# ZoikoStream Status Page

## What the status page shows

The ZoikoStream status page (/status) shows published service status, incident history and planned maintenance. Times are shown in your local timezone; hover any timestamp to see it in UTC. For the current status of the service, check /status directly.

The page shows an overall status heading (for example "All systems operational"), a list of components with their current status, and, when there are any, active incidents, scheduled maintenance, past incidents and recent maintenance. If nothing has been published, it says "No incidents have been published."

## Incidents

Each incident shows its status (investigating, identified, monitoring or resolved), when it started and, if resolved, when it was resolved. An incident can also show "What you can do", "Remaining work", and "Next update by" when a next update has been promised. An incident that was resolved and then reopened is labeled "Reopened".

Every published update stays visible. A correction does not overwrite what it corrects: the correction is labeled "Corrects v" followed by the version number, and the corrected update is labeled "Later corrected".

## Maintenance

Maintenance entries show their start and end times and, when relevant, a previous start time, when the work started, completed or was cancelled, and any remaining work. Emergency work is labeled separately from planned maintenance.

## Subscribing to status updates

Status email contains incident and maintenance notices for the parts of the platform you care about, and nothing else. It is not a marketing list.

1. Go to /status and select "Get notified" (or scroll to "Subscribe to status updates").
2. Enter your email address.
3. Optionally choose components and regions. Select none to be notified about everything.
4. Select "Subscribe".
5. Check your inbox and open the confirmation link. Nothing else is sent until you confirm the address.

After you confirm, the page says: "Your email is confirmed. You'll receive status updates for your selected components."

## Managing or stopping status email

Use the manage link from a status email to open "Manage your status subscription". There you can change your components and regions and select "Save preferences", or select "Unsubscribe from status email".

Status email is a separate channel. Changing or unsubscribing from it never affects account, security, billing or privacy email.

## If the status page does not load

If the page cannot reach the status API, it says "We can't load the status page right now." That itself may be a sign of a wider problem; try again shortly.
