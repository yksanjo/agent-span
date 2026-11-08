import json
import unittest

from agent_span import (
    MAX_ATTR_VALUE_LEN, Span, Tracer, by_name_totals, critical_path,
    error_spans, export_json, validate,
)


class FakeClock:
    def __init__(self):
        self.t = 100.0
    def __call__(self):
        self.t += 1.0
        return self.t


def make_tracer():
    ids = iter(f"id{i}" for i in range(100))
    return Tracer(clock=FakeClock(), id_source=lambda: next(ids),
                  trace_id="trace1")


class TestSpanBasics(unittest.TestCase):
    def test_duration(self):
        tr = make_tracer()
        with tr.span("a") as s:
            pass
        self.assertEqual(s.duration, 1.0)
        self.assertEqual(s.status, "ok")

    def test_nesting_parent_ids(self):
        tr = make_tracer()
        with tr.span("root") as r:
            with tr.span("child") as c:
                pass
        self.assertIsNone(r.parent_id)
        self.assertEqual(c.parent_id, r.span_id)
        self.assertEqual(r.trace_id, c.trace_id)

    def test_finished_order_is_inner_first(self):
        tr = make_tracer()
        with tr.span("root"):
            with tr.span("child"):
                pass
        names = [s.name for s in tr.finished()]
        self.assertEqual(names, ["child", "root"])

    def test_exception_marks_error_and_reraises(self):
        tr = make_tracer()
        with self.assertRaises(RuntimeError):
            with tr.span("boom") as s:
                raise RuntimeError("tool down")
        self.assertEqual(s.status, "error")
        self.assertIn("tool down", s.status_detail)

    def test_set_status_validation(self):
        s = Span("x", "t", "s1", None, 0.0)
        with self.assertRaises(ValueError):
            s.set_status("weird")

    def test_long_attr_clipped(self):
        tr = make_tracer()
        with tr.span("a", attrs={"big": "x" * 500}) as s:
            pass
        self.assertEqual(len(s.attrs["big"]), MAX_ATTR_VALUE_LEN + 3)

    def test_events_recorded(self):
        tr = make_tracer()
        with tr.span("a") as s:
            s.add_event("cache_hit", ts=s.start + 0.5, attrs={"k": "v"})
        self.assertEqual(s.events[0].name, "cache_hit")
        self.assertEqual(s.events[0].attrs, {"k": "v"})


class TestValidate(unittest.TestCase):
    def test_clean_tree_no_violations(self):
        tr = make_tracer()
        with tr.span("root"):
            with tr.span("child"):
                pass
        self.assertEqual(validate(tr.finished()), [])

    def test_negative_duration_flagged(self):
        s = Span("bad", "t", "s1", None, start=10.0, end=5.0)
        kinds = [v.kind for v in validate([s])]
        self.assertIn("negative_duration", kinds)

    def test_unfinished_flagged(self):
        s = Span("hung", "t", "s1", None, start=1.0)
        kinds = [v.kind for v in validate([s])]
        self.assertIn("unfinished", kinds)

    def test_orphan_flagged(self):
        s = Span("lost", "t", "s1", "missing-parent", 1.0, end=2.0)
        kinds = [v.kind for v in validate([s])]
        self.assertIn("orphan", kinds)

    def test_child_outside_parent_window(self):
        p = Span("p", "t", "p1", None, 10.0, end=20.0)
        c = Span("c", "t", "c1", "p1", 15.0, end=25.0)
        kinds = [v.kind for v in validate([p, c])]
        self.assertIn("outside_parent_window", kinds)

    def test_duplicate_ids_flagged(self):
        a = Span("a", "t", "same", None, 1.0, end=2.0)
        b = Span("b", "t", "same", None, 3.0, end=4.0)
        kinds = [v.kind for v in validate([a, b])]
        self.assertIn("duplicate_span_id", kinds)

    def test_event_outside_span_flagged(self):
        s = Span("s", "t", "s1", None, 10.0, end=20.0)
        s.add_event("late", ts=25.0)
        kinds = [v.kind for v in validate([s])]
        self.assertIn("event_outside_span", kinds)


class TestAnalysis(unittest.TestCase):
    def test_by_name_totals(self):
        tr = make_tracer()
        with tr.span("search"):
            pass
        with tr.span("search"):
            pass
        totals = by_name_totals(tr.finished())
        self.assertEqual(totals["search"], 2.0)

    def test_critical_path_picks_longest_branch(self):
        tr = make_tracer()
        with tr.span("root"):
            with tr.span("short"):
                pass
            with tr.span("long"):
                tr.clock()  # burn extra time
        path = critical_path(tr.finished())
        self.assertEqual([s.name for s in path], ["root", "long"])

    def test_critical_path_empty(self):
        self.assertEqual(critical_path([]), [])

    def test_error_spans(self):
        tr = make_tracer()
        with tr.span("ok"):
            pass
        try:
            with tr.span("bad"):
                raise RuntimeError("x")
        except RuntimeError:
            pass
        self.assertEqual([s.name for s in error_spans(tr.finished())], ["bad"])


class TestExport(unittest.TestCase):
    def test_export_shape(self):
        tr = make_tracer()
        with tr.span("root", attrs={"k": "v"}) as r:
            with tr.span("child"):
                pass
        data = json.loads(export_json(tr.finished()))
        self.assertEqual(len(data), 2)
        child = next(d for d in data if d["name"] == "child")
        self.assertEqual(child["trace_id"], "trace1")
        self.assertEqual(child["parent_span_id"], r.span_id)
        self.assertEqual(child["status"]["code"], "ok")
        root = next(d for d in data if d["name"] == "root")
        self.assertEqual(root["attributes"], {"k": "v"})
        self.assertIsNone(root["parent_span_id"])

    def test_export_events(self):
        tr = make_tracer()
        with tr.span("a") as s:
            s.add_event("e1", ts=s.start + 0.5)
        data = json.loads(export_json(tr.finished()))
        self.assertEqual(data[0]["events"][0]["name"], "e1")


if __name__ == "__main__":
    unittest.main()
