"""ZoikoStream Guide — the public, anonymous website assistant (ZST-PRD-PUB-GUIDE-001).

A separate trust domain from the authenticated platform. Nothing in this package may import
`app.models`, `app.security`, `app.db` or any platform router/service: the public Guide has no
path to tenant, account, event-operator, billing or guest-list data (ZST-ARCH-PUB-GUIDE-001
§2.1). `guide_tests/test_isolation.py` enforces that rule by scanning the imports.

The package is mounted into the platform app by `app.guide.asgi.mount_guide`, which is written
so that a Guide fault (bad config, unreachable Guide database, import error) can never stop the
platform from booting — the Guide must fail independently (PR-041).
"""
