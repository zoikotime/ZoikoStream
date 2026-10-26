"""Guide golden-signal metrics (ZST-ARCH-PUB-GUIDE-001 §20, ZST-RAG §26-27).

In-process counters and histograms, rendered in the Prometheus text format by the internal
/metrics route. Labels must be low-cardinality enums (route, outcome, state, provider) — never
message text, session ids, IPs or anything that identifies a visitor.

    metrics.incr("guide_requests_total", route="messages", outcome="ok")
    metrics.observe("guide_answer_latency_seconds", 1.42, provider="anthropic")
    with metrics.timer("rag_retrieval_seconds"): ...
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager

# Latency buckets (seconds) sized around the 2.5s first-visible-response target.
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 60.0)

_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_LABEL_VALUE = re.compile(r"^[A-Za-z0-9_.:\-]{0,64}$")

_lock = threading.Lock()
_counters: dict[tuple, float] = {}
_histograms: dict[tuple, list] = {}  # key -> [bucket counts..., +Inf count, sum]


def _key(name: str, labels: dict) -> tuple:
    if not _NAME.match(name):
        raise ValueError(f"bad metric name {name!r}")
    items = []
    for k, v in sorted(labels.items()):
        v = str(v)
        if not _NAME.match(k) or not _LABEL_VALUE.match(v):
            # Reject rather than truncate: a free-text label is a privacy bug, not a metric.
            raise ValueError(f"bad metric label {k}={v!r}")
        items.append((k, v))
    return (name, tuple(items))


def incr(name: str, value: float = 1.0, **labels) -> None:
    key = _key(name, labels)
    with _lock:
        _counters[key] = _counters.get(key, 0.0) + value


def observe(name: str, value: float, **labels) -> None:
    key = _key(name, labels)
    with _lock:
        h = _histograms.setdefault(key, [0] * (len(BUCKETS) + 1) + [0.0])
        for i, upper in enumerate(BUCKETS):
            if value <= upper:
                h[i] += 1
        h[len(BUCKETS)] += 1
        h[-1] += value


@contextmanager
def timer(name: str, **labels):
    start = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - start, **labels)


def _fmt_labels(items: tuple, extra: tuple = ()) -> str:
    pairs = list(items) + list(extra)
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in pairs) + "}"


def render() -> str:
    """Prometheus text exposition format."""
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        histograms = {k: list(v) for k, v in _histograms.items()}
    for name in sorted({k[0] for k in counters}):
        lines.append(f"# TYPE {name} counter")
        for (n, items), v in sorted(counters.items()):
            if n == name:
                lines.append(f"{name}{_fmt_labels(items)} {v:g}")
    for name in sorted({k[0] for k in histograms}):
        lines.append(f"# TYPE {name} histogram")
        for (n, items), h in sorted(histograms.items()):
            if n != name:
                continue
            for i, upper in enumerate(BUCKETS):
                lines.append(f"{name}_bucket{_fmt_labels(items, (('le', f'{upper:g}'),))} {h[i]}")
            lines.append(f"{name}_bucket{_fmt_labels(items, (('le', '+Inf'),))} "
                         f"{h[len(BUCKETS)]}")
            lines.append(f"{name}_sum{_fmt_labels(items)} {h[-1]:g}")
            lines.append(f"{name}_count{_fmt_labels(items)} {h[len(BUCKETS)]}")
    return "\n".join(lines) + "\n"


def snapshot_counter(name: str, **labels) -> float:
    """Current counter value (tests and readiness checks)."""
    with _lock:
        return _counters.get(_key(name, labels), 0.0)


def reset() -> None:
    """Tests only."""
    with _lock:
        _counters.clear()
        _histograms.clear()
