"""The Guide is a separate trust domain: no platform imports, no privileged routes
(ZST-ARCH-PUB-GUIDE-001 §2.1, AG-01; PRD PR-038)."""
import ast
from pathlib import Path

from app.guide.router import router

GUIDE_DIR = Path(__file__).resolve().parents[1] / "app" / "guide"


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


def test_guide_never_imports_platform_modules():
    offenders = []
    for py in GUIDE_DIR.rglob("*.py"):
        for module, level in _imports(py):
            if level == 0 and module.startswith("app.") and not module.startswith("app.guide"):
                offenders.append(f"{py.relative_to(GUIDE_DIR)}: {module}")
            # Relative imports climbing out of app/guide (e.g. `from ...security import`).
            depth = len(py.relative_to(GUIDE_DIR).parts) - 1
            if level > depth + 1:
                offenders.append(f"{py.relative_to(GUIDE_DIR)}: {'.' * level}{module}")
    assert not offenders, offenders


def test_public_route_registry_has_no_privileged_paths():
    paths = {r.path for r in router.routes}
    forbidden = ("admin", "operator", "org", "billing", "account", "tenant", "event/",
                 "events/", "telemetry", "go-live", "golive", "refund", "guest")
    for path in paths:
        assert not any(f in path.lower() for f in forbidden), path


def test_route_surface_is_the_documented_one():
    routes = {(m, r.path) for r in router.routes for m in r.methods if m != "HEAD"}
    documented = {
        ("GET", "/bootstrap"),
        ("POST", "/sessions"),
        ("PATCH", "/sessions/{session_id}/context"),
        ("POST", "/sessions/{session_id}/end"),
        ("POST", "/sessions/{session_id}/support-flows"),
        ("POST", "/sessions/{session_id}/support-flows/{flow_id}/steps"),
        ("POST", "/sessions/{session_id}/messages"),
        ("PUT", "/sessions/{session_id}/consents/{purpose}"),
        ("POST", "/sessions/{session_id}/lead-flows"),
        ("PATCH", "/sessions/{session_id}/lead-flows/{flow_id}"),
        ("POST", "/sessions/{session_id}/lead-flows/{flow_id}/submit"),
        ("POST", "/sessions/{session_id}/handoffs"),
        ("POST", "/sessions/{session_id}/feedback"),
    }
    assert len(documented) == 13  # API §2: 13 operations
    assert routes == documented
