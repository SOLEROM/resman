from modules.event_bus import EventBus


def test_subscribe_emit_roundtrip():
    bus = EventBus()
    received = []
    bus.subscribe("ping", lambda p: received.append(p))
    bus.emit("ping", {"x": 1})
    assert received == [{"x": 1}]


def test_handler_failure_does_not_propagate():
    bus = EventBus()
    received = []
    bus.subscribe("evt", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("evt", lambda p: received.append(p))
    bus.emit("evt", {"k": "v"})
    assert received == [{"k": "v"}]


def test_unsubscribe_removes_handler():
    bus = EventBus()
    seen = []
    fn = lambda p: seen.append(1)
    bus.subscribe("x", fn)
    bus.unsubscribe("x", fn)
    bus.emit("x")
    assert seen == []


def test_emit_without_subscribers_is_safe():
    bus = EventBus()
    bus.emit("nobody-listens", {"a": 1})  # must not raise


def test_multiple_subscribers_run_in_order():
    bus = EventBus()
    calls = []
    bus.subscribe("e", lambda p: calls.append("a"))
    bus.subscribe("e", lambda p: calls.append("b"))
    bus.emit("e")
    assert calls == ["a", "b"]
