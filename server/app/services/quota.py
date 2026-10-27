"""Pure helpers for resolving configured entitlements into enforceable limits."""


def resolve_effective_limit(
    *,
    has_subscription: bool,
    entitled: bool,
    plan_limit: int | float | None,
    fallback_limit: int | float | None,
    hard_ceiling: int | float | None = None,
) -> dict:
    """Resolve plan/default/hard-ceiling semantics without conflating null states.

    A null limit on an entitled plan is unlimited. A missing plan/default is unconfigured,
    even when a hard ceiling exists: a ceiling constrains an allowance but does not grant one.
    """
    if has_subscription:
        configured = plan_limit is not None or entitled
        limit = plan_limit
        source = "plan" if plan_limit is not None else ("unlimited_plan" if entitled else "unconfigured")
    else:
        configured = fallback_limit is not None
        limit = fallback_limit
        source = "platform_default" if configured else "unconfigured"

    if configured and hard_ceiling is not None:
        if limit is None or hard_ceiling < limit:
            limit = hard_ceiling
            source = "plan_and_platform_ceiling" if has_subscription else "default_and_platform_ceiling"

    return {"limit": limit, "configured": configured, "source": source}
