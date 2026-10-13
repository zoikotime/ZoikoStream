"""The platform mounts the Guide, and a Guide failure never stops the platform
(PR-041, ARCH-AT-01/18)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.guide import mount as mount_mod
from app.guide.asgi import MOUNT_PATH


def test_platform_app_mounts_guide_before_spa_catch_all():
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    assert MOUNT_PATH in paths
    spa = [i for i, p in enumerate(paths) if p == "/{path:path}"]
    assert not spa or paths.index(MOUNT_PATH) < spa[0]


def test_guide_bootstrap_reachable_through_platform_app():
    from app.main import app
    # No `with` block: the platform lifespan (tickers) must not start in a Guide test.
    res = TestClient(app).get(f"{MOUNT_PATH}/bootstrap")
    assert res.status_code == 200 and res.json()["assistant"]["name"] == "Guide"


def test_guide_import_failure_does_not_raise(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def failing(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and name == "asgi":
            raise ImportError("simulated broken guide")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", failing)
    host = FastAPI()
    assert mount_mod.mount_guide(host) is False

    @host.get("/health")
    def health():
        return {"status": "ok"}

    monkeypatch.setattr(builtins, "__import__", real_import)
    assert TestClient(host).get("/health").status_code == 200
