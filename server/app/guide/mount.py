"""The only Guide symbol the platform imports. Deliberately dependency-free: the Guide package
itself is imported INSIDE the try, so even an ImportError in Guide code cannot stop the
platform from booting (PR-041)."""
import logging

log = logging.getLogger(__name__)


def mount_guide(platform_app) -> bool:
    try:
        from .asgi import mount_guide as _mount
    except Exception:
        log.exception("ZoikoStream Guide failed to import; platform continues without it")
        return False
    return _mount(platform_app)
