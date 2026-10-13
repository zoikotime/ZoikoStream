---
source_id: trust-security
title: ZoikoStream Trust Center
source_type: trust
canonical_path: "/trust"
risk_class: HIGH
authority_rank: 90
version: "2026.10"
origin_files: [client/src/pages/Trust.jsx, server/app/routers/trust.py]
---

# ZoikoStream Trust Center

## What the Trust Center is

The ZoikoStream Trust Center (/trust) is headed "Security, transparently." It contains published security advisories, the documentation ZoikoStream can share under an approved request, and a protected channel for reporting a vulnerability. Times are shown in your local timezone; hover any timestamp to see it in UTC.

## Security advisories

The Trust Center lists open advisories and closed advisories. If there are none open, it shows "No open security advisories."

Each advisory can show a summary and fields such as: What this means for you, Affected versions, Scope, CVSS vector, Immediate mitigation, Workaround, Fixed version, Remediation steps, and Deadline. If no workaround exists, it says "No workaround is available." If no deadline is set, it says "No deadline has been set for this advisory."

When an advisory has remediation steps:

- If the action is mandatory, it says: "Required action: apply this update as soon as you are able."
- Otherwise it says: "Recommended: apply this update during your normal change process."

Every published version of an advisory stays readable through "Show published history", and a version that was later corrected is labeled "Later updated".

## What a closed advisory means

A closed advisory is closed on ZoikoStream's side. It does not confirm the update has been applied in your organization.

## Requesting trust documentation

Confidential documents are released under an approved request. Approved access is issued to one address, for one document, purpose and scope, and it expires, so request it for the address that will actually read it. Each listed document is marked either "Request required" or "Public". If no documents are published, the page says "No documents are published yet."

To request a document:

1. Go to /trust and select "Request documents" (or scroll to "Request trust documentation").
2. Select a document, then a purpose and a scope. Only the purposes and scopes that document allows are offered.
3. Enter your email. Optionally enter your company and what you are reviewing.
4. Select "Submit request".

After you submit, the page shows your request reference. ZoikoStream reviews requests individually and will email you when a decision has been made. Nothing is released automatically.

## Reporting a security issue

Reports go to ZoikoStream's security team, not to general support. ZoikoStream will confirm receipt and tell you what it finds.

The Trust Center states its program terms:

- Paid bounty: No
- Public credit: Not offered
- Coordinated disclosure policy: None published. ZoikoStream will agree timing with you directly.

Do not send credentials. ZoikoStream never needs a password, API key or private key to reproduce an issue, and a report containing one will be rejected rather than stored.

To report an issue:

1. Go to /trust and select "Report an issue" (or scroll to "Report a security issue").
2. Enter your email. Your name is optional.
3. Enter a one-line summary of the issue and select a category.
4. Optionally name the affected service.
5. Describe what happens, what you expected, and why it matters. Optionally add steps to reproduce.
6. Select "Submit report".

After you submit, the page shows your report reference, and ZoikoStream emails you a private link to follow it. Keep that link: it is the only way to check status without an account. The link is also shown once on the page in case the email does not arrive.

## If the Trust Center does not load

If the page cannot load, it asks you to try again shortly. If you are trying to report a security issue urgently, the report form is still on the page and will still submit.
