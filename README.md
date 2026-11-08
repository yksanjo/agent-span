# agent-span

Lightweight tracing for agent runs, in dependency-free Python.

Agent observability today is mostly unstructured logs. This library gives
an agent run the shape tracing systems gave services: nested spans with
timing, status, attributes and events, exportable as plain JSON. When a
run goes wrong you can see which step ate the time and which tool failed,
instead of grepping a transcript.

## Usage

```python
from agent_span import Tracer, validate, critical_path, export_json

tracer = Tracer()
with tracer.span("agent_run", attrs={"task": "book a table"}) as run:
    with tracer.span("search_restaurants", attrs={"query": "sushi"}):
        ...                                   # work
    with tracer.span("book_table") as s:
        s.set_status("error", "no 7pm tables")

spans = tracer.finished()
print(validate(spans))          # structural violations, if any
print(critical_path(spans))     # slowest root-to-leaf chain
blob = export_json(spans)       # portable OTel-flavored JSON
```

Spans close themselves (context manager), and an exception escaping a span
marks it `error` with the exception detail before re-raising. Every span
carries a `trace_id`, `span_id`, `parent_id`, start/end, status,
attributes and timestamped events.

## What you get back

- `validate(spans)` - checks the trace holds together: no negative
  durations, no unfinished spans, no orphans, children inside their
  parent's time window, no duplicate span ids, events inside their span.
- `by_name_totals(spans)` - total duration per step name (where the
  retries went).
- `critical_path(spans)` - the slowest root-to-leaf chain.
- `error_spans(spans)` - what failed and why.
- `export_json(spans)` - one object per span with OTel-flavored field
  names; ingest it anywhere JSON goes.

## Demo and tests

```
python3 demo.py                     # synthetic run, no network
python3 -m unittest test_agent_span -v   # 20 tests
```

## Honest limits

- **Single-threaded, in-process.** Spans nest via a stack; there is no
  async or cross-process context propagation. Concurrent agent workers
  need one tracer each, merged afterward (merging is on you).
- **Wall-clock durations.** Timing comes from the clock you inject
  (`time.time` by default); it includes anything else the machine was
  doing. For deterministic tests, pass a fake clock like the demo does.
- **Validation is structural, not semantic.** `validate` proves the trace
  is well-formed, not that the agent did the right things.
- **Attributes are clipped at 256 chars.** Spans are metadata; do not
  route payload data through attributes.
- **Not OTLP on the wire.** `export_json` borrows field names but does
  not implement the OpenTelemetry protocol; feeding a real collector
  needs an adapter.
- **Clock skew between merged traces is not detected.** If you merge
  spans from two machines, `validate` may flag windows that drifted.

## License

MIT. See [LICENSE](LICENSE).
