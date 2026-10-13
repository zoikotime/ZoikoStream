---
id: developer
title: Developer platform
version: "2026.10"
path: /organization/developers
api: v1
---

## API keys
Organization admins create API keys in the developer platform. A key is shown only once when it is created; ZoikoStream stores only a hash. Keys can have an expiry date — a warning is sent seven days before a key expires — and can be rotated or revoked.

## Authentication
Send the API key from your server, never from a browser or mobile app. Keep signing material and entitlement decisions on your server and give clients only short-lived, client-safe credentials.

## Webhooks
A webhook endpoint receives event notifications such as access link changes. New endpoints must be verified before deliveries start. Each endpoint has a signing secret used to sign deliveries; rotating the secret keeps the previous secret valid for an overlap period. Endpoints that fail repeatedly are marked degraded and can be disabled automatically.

## Data exports
Organization admins can request a data export from the developer platform; it is prepared in the background and downloaded from the export list.

## API reference
The platform API is versioned (v1). Guide does not show raw keys or secrets, and never calls APIs on your behalf outside its approved actions.
