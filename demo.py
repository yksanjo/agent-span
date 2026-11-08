"""Synthetic demo: one agent run, traced end to end. Nothing leaves the process."""

from agent_span import (
    Tracer, by_name_totals, critical_path, error_spans, export_json, validate,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        self.t += 0.25
        return self.t
    def work(self, seconds):
        self.t += seconds


tr = Tracer(clock=FakeClock(), trace_id="demo-run-1")
clock = tr.clock

with tr.span("agent_run", attrs={"task": "book a table"}) as run:
    with tr.span("plan"):
        clock.work(0.5)
    with tr.span("search_restaurants", attrs={"query": "sushi downtown"}) as s:
        clock.work(1.5)
        s.add_event("cache_miss", ts=clock())
    with tr.span("search_restaurants", attrs={"query": "sushi midtown"}):
        clock.work(0.75)  # second, narrower search
    with tr.span("book_table") as s:
        clock.work(1.0)
        s.set_status("error", "no 7pm tables")
    with tr.span("report"):
        clock.work(0.25)

spans = tr.finished()

print("span tree (duration):")
kids = {}
for s in spans:
    kids.setdefault(s.parent_id, []).append(s)
def show(s, depth):
    print(f"  {'  ' * depth}{s.name:20} {s.duration:5.2f}s  {s.status}")
    for k in kids.get(s.span_id, []):
        show(k, depth + 1)
for root in kids.get(None, []):
    show(root, 0)

print(f"\ntotals by name: {by_name_totals(spans)}")
print("critical path:", " -> ".join(s.name for s in critical_path(spans)))
print("errors:", [(s.name, s.status_detail) for s in error_spans(spans)])
print("validation:", validate(spans) or "no violations")
print(f"\nexport_json: {len(export_json(spans))} bytes of portable JSON")
