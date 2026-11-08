"""agent_span: lightweight tracing for agent runs, OTel-inspired.

Agent observability today is mostly unstructured logs. This library gives
an agent run the shape tracing systems gave services: nested spans with
timing, status, attributes, and events, exportable as plain JSON.

    tracer = Tracer()
    with tracer.span("plan") as root:
        with tracer.span("search", attrs={"q": "pizza"}) as s:
            ...                  # s.end() is automatic
        with tracer.span("book") as s:
            s.set_status("error", "no tables")

    tree = tracer.finished()     # validated span records
    report = validate(tree)      # invariant violations, if any
    blob = export_json(tree)     # portable JSON

Dependency-free, deterministic when given a clock, no network access.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

MAX_ATTR_VALUE_LEN = 256  # attributes are metadata, not data dumps


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_ATTR_VALUE_LEN:
        return value[:MAX_ATTR_VALUE_LEN] + "..."
    return value


@dataclass
class SpanEvent:
    name: str
    ts: float
    attrs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """One unit of work inside a run. Not thread-safe by design."""
    name: str
    trace_id: str
    span_id: str
    parent_id: Optional[str]
    start: float
    end: Optional[float] = None
    status: str = "ok"               # 'ok' | 'error'
    status_detail: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)

    @property
    def duration(self) -> Optional[float]:
        return None if self.end is None else self.end - self.start

    def set_status(self, status: str, detail: str = "") -> None:
        if status not in ("ok", "error"):
            raise ValueError(f"status must be 'ok' or 'error', got {status!r}")
        self.status = status
        self.status_detail = detail

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = _clip(value)

    def add_event(self, name: str, ts: float, attrs: Optional[Dict[str, Any]] = None) -> None:
        self.events.append(SpanEvent(name=name, ts=ts,
                                     attrs={k: _clip(v) for k, v in (attrs or {}).items()}))


class Tracer:
    """Creates spans and collects them on finish. Single-threaded."""

    def __init__(self, clock=time.time, id_source=None, trace_id: Optional[str] = None) -> None:
        self.clock = clock
        self.id_source = id_source or (lambda: uuid.uuid4().hex[:16])
        self.trace_id = trace_id or (uuid.uuid4().hex)
        self._stack: List[Span] = []
        self._finished: List[Span] = []

    @contextmanager
    def span(self, name: str, attrs: Optional[Dict[str, Any]] = None) -> Iterator[Span]:
        parent = self._stack[-1] if self._stack else None
        s = Span(
            name=name,
            trace_id=self.trace_id,
            span_id=self.id_source(),
            parent_id=parent.span_id if parent else None,
            start=self.clock(),
            attrs={k: _clip(v) for k, v in (attrs or {}).items()},
        )
        self._stack.append(s)
        try:
            yield s
        except Exception as exc:
            s.set_status("error", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._stack.pop()
            s.end = self.clock()
            self._finished.append(s)

    def finished(self) -> List[Span]:
        return list(self._finished)


# ---------------------------------------------------------------------------
# Validation: does this trace hold together?
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Violation:
    span_id: Optional[str]
    kind: str
    detail: str


def validate(spans: List[Span]) -> List[Violation]:
    """Check structural invariants across a set of finished spans."""
    out: List[Violation] = []
    by_id: Dict[str, Span] = {}
    for s in spans:
        if s.span_id in by_id:
            out.append(Violation(s.span_id, "duplicate_span_id", s.name))
        by_id[s.span_id] = s
    for s in spans:
        if s.end is None:
            out.append(Violation(s.span_id, "unfinished", s.name))
            continue
        if s.duration < 0:
            out.append(Violation(s.span_id, "negative_duration",
                                 f"{s.name}: {s.duration}"))
        if s.parent_id is not None:
            parent = by_id.get(s.parent_id)
            if parent is None:
                out.append(Violation(s.span_id, "orphan",
                                     f"{s.name}: parent {s.parent_id} missing"))
            elif parent.end is not None:
                if s.start < parent.start or s.end > parent.end:
                    out.append(Violation(
                        s.span_id, "outside_parent_window",
                        f"{s.name} escapes {parent.name}"))
        for ev in s.events:
            if ev.ts < s.start or (s.end is not None and ev.ts > s.end):
                out.append(Violation(s.span_id, "event_outside_span",
                                     f"{s.name}: event {ev.name}"))
    return out


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def by_name_totals(spans: List[Span]) -> Dict[str, float]:
    """Total self-reported duration per span name."""
    totals: Dict[str, float] = {}
    for s in spans:
        if s.duration is not None:
            totals[s.name] = totals.get(s.name, 0.0) + s.duration
    return totals


def critical_path(spans: List[Span]) -> List[Span]:
    """The root->leaf chain with the largest end-to-end duration."""
    children: Dict[Optional[str], List[Span]] = {}
    for s in spans:
        children.setdefault(s.parent_id, []).append(s)

    best: List[Span] = []

    def walk(s: Span, acc: List[Span], acc_dur: float) -> None:
        nonlocal best
        acc = acc + [s]
        acc_dur += s.duration or 0.0
        kids = children.get(s.span_id, [])
        if not kids:
            cur = sum(x.duration or 0.0 for x in best)
            if acc_dur > cur:
                best = acc
            return
        for k in kids:
            walk(k, acc, acc_dur)

    for root in children.get(None, []):
        walk(root, [], 0.0)
    return best


def error_spans(spans: List[Span]) -> List[Span]:
    return [s for s in spans if s.status == "error"]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_json(spans: List[Span]) -> str:
    """Portable JSON: one object per span, OTel-flavored field names."""
    def one(s: Span) -> Dict[str, Any]:
        return {
            "trace_id": s.trace_id,
            "span_id": s.span_id,
            "parent_span_id": s.parent_id,
            "name": s.name,
            "start_unix": s.start,
            "end_unix": s.end,
            "duration_s": s.duration,
            "status": {"code": s.status, "detail": s.status_detail},
            "attributes": s.attrs,
            "events": [{"name": e.name, "ts": e.ts, "attributes": e.attrs}
                       for e in s.events],
        }
    return json.dumps([one(s) for s in spans], indent=2, sort_keys=True)
