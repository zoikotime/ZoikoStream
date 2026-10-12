"""The only Operator Guide symbol the platform imports.

Failure-isolated like the public Guide's mount: the package is imported INSIDE the try, so an
error anywhere in Guide code is logged and the platform boots without it. Mounted with the
same organization-state gate as the other organization routers (ZST-EC-001 ORG-010), so a
restricted tenant loses Guide together with the rest of its operational access.
"""
import logging

log = logging.getLogger(__name__)


def mount_operator_guide(platform_app) -> bool:
    try:
        from fastapi import Depends

        from app.services.org_state import require_operational_org_access

        from .router import router
        platform_app.include_router(router, prefix="/api",
                                    dependencies=[Depends(require_operational_org_access)])
    except Exception:
        log.exception("Operator Guide failed to mount; platform continues without it")
        return False
    return True
