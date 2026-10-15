from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import queue
import sys
import threading
import time
from types import SimpleNamespace

import curses
import pytest
from wcwidth import wcswidth

from jmsbrowser.brokers import JMSAdapter
from jmsbrowser.client import BrokerError
from jmsbrowser.composer import Composer, TextBuffer
from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.demo import DemoAdapter
from jmsbrowser.outgoing import OutgoingMessage, OutgoingProperty, SendFailure, destination, load_body, load_draft, save_draft
from jmsbrowser.screen import render
from jmsbrowser.state import Controller
from jmsbrowser.ui import TerminalApp


@pytest.mark.parametrize("kind,text,value", [("String", "é🙂", "é🙂"), ("Boolean", "false", False), ("Boolean", "TRUE", True),
    ("Byte", "-128", -128), ("Short", "32767", 32767), ("Int", "2147483647", 2147483647),
    ("Long", "9223372036854775807", 9223372036854775807), ("Float", "1.25", 1.25), ("Double", "-2.5", -2.5)])
def test_property_types_and_ranges(kind, text, value):
    assert OutgoingProperty("value", kind, text).converted() == value


@pytest.mark.parametrize("property", [OutgoingProperty("JMSPriority", "Int", "3"), OutgoingProperty("AND"), OutgoingProperty("foo-bar"),
    OutgoingProperty("1bad"), OutgoingProperty("flag", "Boolean", "yes"), OutgoingProperty("b", "Byte", "128"),
    OutgoingProperty("s", "Short", "32768"), OutgoingProperty("i", "Int", "2147483648"), OutgoingProperty("l", "Long", str(2**63)),
    OutgoingProperty("f", "Float", "1e100"), OutgoingProperty("f", "Double", "NaN"), OutgoingProperty("f", "Double", "Infinity")])
def test_invalid_property_does_not_reach_a_producer(property):
    with pytest.raises(ValueError):
        OutgoingMessage(properties=[property]).validate()


@pytest.mark.parametrize("changes", [{"priority": -1}, {"priority": 10}, {"priority": True}, {"ttl_ms": -1}, {"persistent": "yes"},
    {"ttl_ms": 2**63}, {"properties": [OutgoingProperty("x"), OutgoingProperty("x")]}, {"body": "x" * (4 * 1024 * 1024 + 1)}])
def test_invalid_message_settings(changes):
    with pytest.raises(ValueError):
        replace(OutgoingMessage(), **changes).validate()


def test_draft_files_roundtrip_unicode_and_typed_values_without_credentials(tmp_path):
    config = ConnectionConfig("IBM", "ibm", queue_manager="QM1", channel="DEV.APP.SVRCONN", password_env="SECRET")
    target = destination(config, "DEV.ORDERS")
    draft = OutgoingMessage("hello é🙂\nsecond line", 8, False, 60000, "event.test", "request-123", [OutgoingProperty("x", "Long", "123")])
    path = tmp_path / "draft.json"
    save_draft(path, target, draft)
    assert load_draft(path, target) == draft
    assert "SECRET" not in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        save_draft(path, target, OutgoingMessage("replace"))
    assert load_draft(path, target) == draft
    with pytest.raises(ValueError, match="destination differs"):
        load_draft(path, {**target, "queue": "OTHER"})
    body_path = tmp_path / "body.txt"
    body_path.write_bytes(b"\xef\xbb\xbf" + draft.body.encode())
    assert load_body(body_path) == draft.body


def test_multiline_editor_inserts_deletes_and_moves_across_lines():
    buffer = TextBuffer("one\ntwo\nthree", 5)
    buffer.edit("UP", True)
    assert buffer.cursor == 1
    buffer.edit("DOWN", True)
    assert buffer.cursor == 5
    buffer.edit("END", True)
    buffer.edit("ENTER", True)
    buffer.edit("q", True)
    assert buffer.text == "one\ntwo\nq\nthree"
    buffer.edit("HOME", True)
    buffer.edit("DELETE", True)
    buffer.edit("BACKSPACE", True)
    assert buffer.text == "one\ntwo\nthree"


def test_windows_utf16_input_preserves_emoji_in_composer(app):
    app.handle_key("n")
    app.handle_key("é")
    app.handle_key("\ud83d")
    app.handle_key("\ude42")
    assert app.view.composer.fields["body"].text == "é🙂"
    app.handle_key("F2")
    assert len(app.view.composer.prepared.body.encode("utf-8")) == 6


@pytest.fixture
def app():
    config = Config((ConnectionConfig("Demo", "artemis"),))
    controller = Controller(config)
    adapter = DemoAdapter(config.connections[0])
    state = controller.current
    state.status, state.queues = "connected", adapter.list_queues()
    state.queue_index = next(i for i, q in enumerate(state.queues) if q.name == "DEV.ORDERS")
    state.messages = adapter.browse("DEV.ORDERS")
    controller.pane = 2
    app = TerminalApp(controller)
    yield app
    controller.close()


@pytest.mark.parametrize("width,height", [(120, 28), (160, 46), (220, 60)])
def test_composer_fields_tab_mouse_and_modal_isolation(app, width, height):
    original = (app.controller.pane, app.controller.current.queue_index, app.controller.current.message_index)
    app.handle_key("n")
    composer = app.view.composer
    for char in "query n c r q /? 界é🙂":
        app.handle_key(char)
    app.handle_key("ENTER")
    app.handle_key("x")
    assert composer.fields["body"].text == "query n c r q /? 界é🙂\nx"
    assert app.running and not app.view.consumer_inspector
    frame = app.last_frame = render(app.controller, width, height, app.view)
    assert all(wcswidth(line) == width for line in frame.plain_lines())
    assert "Priority" not in composer.fields["body"].text
    app.handle_mouse(1, 7, curses.BUTTON1_CLICKED)
    assert (app.controller.pane, app.controller.current.queue_index, app.controller.current.message_index) == original
    app.handle_key("TAB")
    assert composer.focus == "load_body"
    field = next(hit for hit in frame.composer_hits if hit[0] == "jms_type")
    app.handle_mouse(field[1], field[2], curses.BUTTON1_CLICKED)
    for char in "event.test":
        app.handle_key(char)
    assert composer.fields["jms_type"].text == "event.test"
    app.handle_key("F2")
    assert composer.stage == "preview" and composer.prepared.jms_type == "event.test"
    app.last_frame = render(app.controller, width, height, app.view)
    assert all(wcswidth(line) == width for line in app.last_frame.plain_lines())
    app.handle_key("ESC")
    assert composer.stage == "edit"
    app.handle_key("ESC")
    assert app.view.composer is None and (app.controller.pane, app.controller.current.queue_index, app.controller.current.message_index) == original


def test_file_loading_draft_save_load_errors_preserve_edits(app, tmp_path):
    app.handle_key("n")
    composer = app.view.composer
    body = tmp_path / "message.txt"
    body.write_bytes("plain\ntext é🙂".encode("utf-8"))
    app.handle_key("F3")
    for char in str(body):
        app.handle_key(char)
    app.handle_key("ENTER")
    assert composer.fields["body"].text == "plain\ntext é🙂"
    app.handle_key("F4")
    app.handle_key("\x15")
    path = tmp_path / "saved.json"
    for char in str(path):
        app.handle_key(char)
    app.handle_key("ENTER")
    assert path.exists() and composer.prompt is None
    composer.fields["body"].text = "changed"
    app.handle_key("F5")
    for char in str(path):
        app.handle_key(char)
    app.handle_key("ENTER")
    assert composer.fields["body"].text == "plain\ntext é🙂"
    app.handle_key("F3")
    for char in str(tmp_path / "missing.txt"):
        app.handle_key(char)
    app.handle_key("ENTER")
    assert composer.error and composer.fields["body"].text == "plain\ntext é🙂"


def test_preview_has_no_write_and_send_is_explicit_once_even_with_repeated_input(app, monkeypatch):
    calls = []
    def send(index, epoch, queue, draft):
        calls.append((index, epoch, queue, draft))
        return SimpleNamespace(status="sending", outcome="", receipt=None, error="")
    monkeypatch.setattr(app.controller, "send_message", send)
    app.handle_key("n")
    app.handle_key("x")
    app.handle_key("F2")
    assert not calls
    app.handle_key("TAB")
    app.handle_key("TAB")
    assert app.view.composer.focus == "send"
    app.handle_key("ENTER")
    for key in ("ENTER", "F2", "ESC", "n"):
        app.handle_key(key)
    assert len(calls) == 1 and app.view.composer.stage == "sending"
    app.view.composer.job.status = "sent"
    app.view.composer.job.receipt = {"JMSMessageID": "ID:new", "size": 1, "demo": True}
    app.handle_key("ENTER")
    assert app.view.composer is None and len(calls) == 1


def test_review_click_cannot_become_a_send_on_same_coordinates(app, monkeypatch):
    monkeypatch.setattr(app.controller, "send_message", lambda *args: pytest.fail("unexpected send"))
    app.handle_key("n")
    app.last_frame = render(app.controller, 160, 46, app.view)
    action, x, y, _, _ = next(hit for hit in app.last_frame.composer_hits if hit[0] == "review")
    app.handle_mouse(x, y, curses.BUTTON1_PRESSED)
    assert app.view.composer.stage == "edit"
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert app.view.composer.stage == "preview"
    app.last_frame = render(app.controller, 160, 46, app.view)
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert app.view.composer.stage == "preview"


def test_typed_property_controls_validation_and_scroll_into_view(app):
    app.handle_key("n")
    composer = app.view.composer
    composer.properties[0].name.text = "enabled"
    composer.focus = "p:0:kind"
    app.handle_key("ENTER")
    assert composer.properties[0].kind == "Boolean"
    composer.properties[0].value.text = "true"
    app.handle_key("F6")
    for char in "count":
        app.handle_key(char)
    for _ in range(3):
        composer.focus = "p:1:kind"
        app.handle_key("ENTER")
    composer.properties[1].value.text = "12"
    frame = app.last_frame = render(app.controller, 120, 28, app.view)
    assert "count" in "\n".join(frame.plain_lines())
    app.handle_key("F2")
    assert composer.prepared.validate() == [("enabled", "Boolean", True), ("count", "Short", 12)]


def test_shared_jms_sender_uses_explicit_options_typed_setters_and_closes_producer(monkeypatch):
    setters, sends, closed = {}, [], []
    class Message:
        def __getattr__(self, name):
            if name.startswith("set"):
                return lambda *values: setters.__setitem__(name, values)
            return lambda: {"getJMSMessageID": "ID:sent", "getJMSTimestamp": 123, "getJMSExpiration": 60123}[name]
    producer = SimpleNamespace(send=lambda *args: sends.append(args), close=lambda: closed.append(True))
    session = SimpleNamespace(createTextMessage=lambda body: Message(), createQueue=lambda name: name, createProducer=lambda queue: producer)
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JLong=int, JInt=int, JFloat=float))
    adapter = JMSAdapter(ConnectionConfig("Test", "artemis"))
    adapter.session = session
    draft = OutgoingMessage("é", 8, False, 60000, "event", "req-1", [OutgoingProperty("count", "Int", "7"), OutgoingProperty("flag", "Boolean", "true")])
    receipt = adapter.send_message("Q", draft)
    assert len(sends) == 1 and sends[0][1:] == (1, 8, 60000) and closed == [True]
    assert setters["setIntProperty"] == ("count", 7) and setters["setBooleanProperty"] == ("flag", True)
    assert setters["setJMSType"] == ("event",) and setters["setJMSCorrelationID"] == ("req-1",)
    assert receipt["JMSMessageID"] == "ID:sent" and receipt["size"] == 2
    producer.send = lambda *args: (_ for _ in ()).throw(TimeoutError("Connection lost"))
    with pytest.raises(SendFailure) as failure:
        adapter.send_message("Q", draft)
    assert failure.value.outcome == "unknown" and len(closed) == 2
    session.createProducer = lambda queue: (_ for _ in ()).throw(RuntimeError("Access denied"))
    with pytest.raises(SendFailure) as failure:
        adapter.send_message("Q", draft)
    assert failure.value.outcome == "not_sent"


def test_controller_send_snapshot_destination_and_background_refresh(monkeypatch):
    started, release = threading.Event(), threading.Event()
    config = Config((ConnectionConfig("Test", "artemis"),))
    controller = Controller(config)
    state = controller.current
    adapter = DemoAdapter(config.connections[0])
    state.status, state.queues = "connected", adapter.list_queues()
    state.queue_index = next(i for i, q in enumerate(state.queues) if q.name == "DEV.ORDERS")
    state.executor = ThreadPoolExecutor(max_workers=1)
    class Client:
        events = queue.Queue()
        closed = False
        def request(self, operation, **arguments):
            if operation == "send":
                started.set()
                release.wait(3)
                return adapter.send_message(arguments["queue"], OutgoingMessage.from_dict(arguments["message"]))
            if operation == "queue_depth":
                return {"name": arguments["queue"], "depth": adapter.queue_depth(arguments["queue"])}
            return [message.to_dict() for message in adapter.browse(arguments["queue"])]
        def close(self):
            pass
    state.client = Client()
    try:
        draft = OutgoingMessage("original", properties=[OutgoingProperty("x", "Int", "1")])
        job = controller.send_message(0, state.epoch, "DEV.ORDERS", draft)
        assert started.wait(1)
        draft.properties.append(OutgoingProperty("later", "String", "change"))
        with pytest.raises(ValueError, match="already"):
            controller.send_message(0, state.epoch, "DEV.ORDERS", draft)
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            controller.poll()
            if job.status == "sent" and not state.busy and not state.depth_jobs:
                break
            time.sleep(0.005)
        assert job.status == "sent" and state.selected_queue.depth == 4
        assert state.messages[-1].body == "original" and state.messages[-1].properties == {"x": 1}
        with pytest.raises(ValueError, match="connection changed"):
            controller.send_message(0, state.epoch + 1, "DEV.ORDERS", draft)
    finally:
        release.set()
        controller.close()


def test_unknown_send_result_has_no_resend_action_and_draft_can_be_saved(app):
    app.handle_key("n")
    composer = app.view.composer
    app.handle_key("F2")
    composer.stage = "sending"
    composer.job = SimpleNamespace(status="error", outcome="unknown", error="Send timed out", receipt=None)
    composer.sync()
    assert composer.stage == "error" and composer.order() == ["save_draft", "close"]
    frame = render(app.controller, 120, 28, app.view)
    assert "Delivery is unknown" in "\n".join(frame.plain_lines())


def test_send_cancelled_before_dispatch_never_writes_to_the_broker(app):
    state = app.controller.current
    blocker_started, unblock = threading.Event(), threading.Event()
    calls = []
    class Client:
        events = queue.Queue()
        closed = False
        def request(self, *args, **kwargs):
            calls.append((args, kwargs))
        def abort(self):
            self.closed = True
        def close(self):
            self.closed = True
    state.client = Client()
    executor = state.executor = ThreadPoolExecutor(max_workers=1)
    def blocker():
        blocker_started.set()
        unblock.wait(3)
    executor.submit(blocker)
    assert blocker_started.wait(1)
    try:
        job = app.controller.send_message(0, state.epoch, state.selected_queue.name, OutgoingMessage("pending"))
        app.controller.disconnect()
        assert job.status == "error" and job.outcome == "not_sent"
    finally:
        unblock.set()
        executor.shutdown(wait=True, cancel_futures=True)
    assert not calls


def test_worker_send_timeout_is_unknown_and_is_not_retried():
    from jmsbrowser.client import WorkerClient
    writes = []
    client = WorkerClient.__new__(WorkerClient)
    client.config = SimpleNamespace(timeout_seconds=0.01, name="Slow broker")
    client.closed, client.lock, client.responses = False, threading.Lock(), queue.Queue()
    client.process = SimpleNamespace(stdin=SimpleNamespace(write=writes.append, flush=lambda: None), poll=lambda: None, kill=lambda: None)
    with pytest.raises(BrokerError) as error:
        client.request("send", queue="Q", message=OutgoingMessage("payload").to_dict())
    assert error.value.outcome == "unknown" and client.closed and len(writes) == 1


def test_success_refreshes_depth_even_when_an_earlier_depth_result_was_pending(app):
    state = app.controller.current
    adapter = DemoAdapter(state.config)
    started, release = threading.Event(), threading.Event()
    state.executor = ThreadPoolExecutor(max_workers=1)
    class Client:
        events = queue.Queue()
        closed = False
        depth_calls = 0
        def request(self, operation, **arguments):
            if operation == "queue_depth":
                self.depth_calls += 1
                depth = adapter.queue_depth(arguments["queue"])
                if self.depth_calls == 1:
                    started.set()
                    release.wait(3)
                return {"name": arguments["queue"], "depth": depth}
            if operation == "send":
                return adapter.send_message(arguments["queue"], OutgoingMessage.from_dict(arguments["message"]))
            return [message.to_dict() for message in adapter.browse(arguments["queue"])]
        def close(self):
            pass
    state.client = Client()
    try:
        app.controller.refresh_queue_depth()
        assert started.wait(1)
        job = app.controller.send_message(0, state.epoch, state.selected_queue.name, OutgoingMessage("new"))
        release.set()
        state.executor.submit(lambda: None).result(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.controller.poll()
            if job.status == "sent" and not state.busy and not state.depth_jobs:
                break
            time.sleep(0.005)
        assert state.client.depth_calls == 2 and state.selected_queue.depth == 4
    finally:
        release.set()
