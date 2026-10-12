"""ZoikoStream Guide — operator edition (ZST-UX-GUIDE-001).

The AI operating assistant for signed-in ZoikoStream users: organization admins, event
producers (hosts) and platform staff. It is a SEPARATE product from the public website
assistant in app/guide (which is anonymous and never sees tenant data).

Architecture (ZST-UX-GUIDE-001 §11), one module per box:

    router.py        Guide API — auth binding (platform JWT), request shaping
    context.py       Context Service — org / workspace / event / environment
    policy.py        Policy Adapter — platform roles + edit rules; the model never decides access
    tools/           Tool Gateway — typed, risk-classed tools over EXISTING platform services
    knowledge/       Retrieval Service — versioned operator documentation
    intents.py       Intent routing — deterministic rules first (§12)
    composer.py      Answer assembly — cards, sources/freshness, bounded model explanation
    llm.py           Model Router — smallest reliable model per task, provider-replaceable
    actions.py       Governed actions — proposals, explicit confirmation, receipts, reconciliation
    attention.py     Proactive notifications — material items only (§20)
    governance.py    Admin governance — availability + capability controls (§6.17)
    audit.py         Audit Service — §16.1 minimum audit event for every interaction

Authority doctrine (§3, §10): Guide interprets and proposes; platform services, permission
checks and readiness gates remain authoritative. Reads go through side-effect-free service
functions after the platform's own authorization; writes go through the platform's own HTTP
endpoints with the user's own credentials, so every existing rule, audit and webhook still
applies. Guide never writes to platform tables directly and never executes R5/R6 operations.
"""
