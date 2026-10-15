from dataclasses import asdict
import queue
import threading
import time

from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.domain import Consumer, ConsumerSnapshot, Message, QueueInfo
from jmsbrowser.state import Controller


class FakeClient:
    pending = threading.Event()
    release = threading.Event()

    def __init__(self, config, demo):
        self.events = queue.Queue()
        self.closed = False
        self.calls = []
        self.depths = {"A": 1, "B": 1}

    def request(self, operation, **options):
        self.calls.append((operation, options))
        if operation in ("connect", "queues"):
            return [asdict(QueueInfo(name, depth)) for name, depth in self.depths.items()]
        if operation == "queue_depth":
            return {"name": options["queue"], "depth": self.depths[options["queue"]]}
        if operation == "consumers":
            return ConsumerSnapshot(options["queue"], [Consumer(consumer_id="c-" + options["queue"])]).to_dict()
        name = options["queue"]
        if name == "A":
            self.pending.set()
            self.release.wait(3)
        return [Message("ID:" + name, "TextMessage", 4, name, 1).to_dict()]

    def abort(self):
        self.closed = True

    def close(self):
        self.closed = True


def wait_until(controller, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        controller.poll()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Background operation did not complete")


def test_stale_browse_cannot_replace_new_queue_selection(monkeypatch):
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", FakeClient)
    FakeClient.pending.clear()
    FakeClient.release.clear()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: FakeClient.pending.is_set())
        controller.select_queue(1)
        assert controller.current.selected_queue.name == "B"
        FakeClient.release.set()
        wait_until(controller, lambda: not controller.current.busy)
        assert controller.current.messages[0].message_id == "ID:B"
        controller.disconnect()
        assert controller.current.status == "disconnected"
        assert not controller.current.messages
    finally:
        FakeClient.release.set()
        controller.close()


def test_provider_connection_loss_clears_connected_dot(monkeypatch):
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", FakeClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        controller.current.client.events.put({"event": "connection_error", "error": "Connection lost"})
        controller.poll()
        assert controller.current.status == "error"
        assert controller.current.error == "Connection lost"
        assert not controller.current.messages
    finally:
        controller.close()


def test_all_depth_refresh_preserves_messages_without_rebrowsing(monkeypatch):
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", FakeClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        state = controller.current
        client = state.client
        messages, selected = state.messages, state.selected_queue.name
        client.depths = {"A": 7, "B": 9}
        client.calls.clear()
        controller.pane = 1
        controller.refresh_queue_depths()
        assert state.all_depths_pending
        controller.refresh_queue_depths()  # coalesce repeated button presses
        wait_until(controller, lambda: not state.depth_jobs)
        assert client.calls == [("queues", {})]
        assert {q.name: q.depth for q in state.queues} == {"A": 7, "B": 9}
        assert state.messages is messages and state.selected_queue.name == selected
        assert controller.pane == 1
        assert not state.all_depths_pending
    finally:
        controller.close()


def test_selected_depth_refresh_does_not_query_or_update_other_queues(monkeypatch):
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", FakeClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        state = controller.current
        client = state.client
        messages = state.messages
        client.depths = {"A": 8, "B": 11}
        client.calls.clear()
        controller.pane = 1
        controller.refresh_queue_depth()
        assert state.depth_pending == {"A"}
        controller.refresh_queue_depth()  # repeated Enter coalesces only this queue
        wait_until(controller, lambda: not state.depth_jobs)
        assert client.calls == [("queue_depth", {"queue": "A"})]
        assert {q.name: q.depth for q in state.queues} == {"A": 8, "B": 1}
        assert state.messages is messages and controller.pane == 1
        assert not state.depth_pending
    finally:
        controller.close()


def test_depth_result_updates_original_queue_after_selection_changes(monkeypatch):
    started, release = threading.Event(), threading.Event()
    class DelayedDepthClient(FakeClient):
        def request(self, operation, **options):
            if operation == "queue_depth":
                started.set()
                release.wait(3)
            return super().request(operation, **options)
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", DelayedDepthClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        controller.current.client.depths["A"] = 8
        controller.refresh_queue_depth()
        assert started.wait(1)
        controller.select_queue(1)
        release.set()
        wait_until(controller, lambda: not controller.current.busy and not controller.current.depth_jobs)
        assert controller.current.selected_queue.name == "B"
        assert controller.current.messages[0].message_id == "ID:B"
        assert {q.name: q.depth for q in controller.current.queues} == {"A": 8, "B": 1}
    finally:
        release.set()
        controller.close()


def test_refresh_all_is_not_ignored_while_selected_depth_is_pending(monkeypatch):
    started, release = threading.Event(), threading.Event()
    class DelayedDepthClient(FakeClient):
        def request(self, operation, **options):
            if operation == "queue_depth":
                started.set()
                release.wait(3)
            return super().request(operation, **options)
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", DelayedDepthClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        state = controller.current
        state.client.calls.clear()
        controller.refresh_queue_depth()
        assert started.wait(1)
        controller.refresh_queue_depths()
        assert state.depth_jobs == 2 and state.all_depths_pending
        release.set()
        wait_until(controller, lambda: not state.depth_jobs)
        assert [call[0] for call in state.client.calls] == ["queue_depth", "queues"]
        assert not state.depth_pending and not state.all_depths_pending
    finally:
        release.set()
        controller.close()


def test_consumer_refresh_preserves_message_selection_and_only_queries_metadata(monkeypatch):
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", FakeClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        state = controller.current
        messages = state.messages
        state.client.calls.clear()
        assert controller.refresh_consumers()
        wait_until(controller, lambda: state.consumer_status == "ready")
        assert state.client.calls == [("consumers", {"queue": "A"})]
        assert state.consumer_snapshot.consumers[0].consumer_id == "c-A"
        assert state.messages is messages and not state.busy
    finally:
        controller.close()


def test_consumer_permission_error_does_not_disconnect_browsing(monkeypatch):
    class DeniedClient(FakeClient):
        def request(self, operation, **options):
            if operation == "consumers":
                raise RuntimeError("Not authorized to inquire consumer status")
            return super().request(operation, **options)
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", DeniedClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        messages = controller.current.messages
        controller.refresh_consumers()
        wait_until(controller, lambda: controller.current.consumer_status == "error")
        assert controller.current.status == "connected"
        assert "Not authorized" in controller.current.consumer_error
        assert controller.current.messages is messages
        controller.refresh_queue_depth()
        wait_until(controller, lambda: not controller.current.depth_jobs)
        assert controller.current.status == "connected"
    finally:
        controller.close()


def test_delayed_consumers_are_discarded_on_queue_change_and_duplicates_coalesced(monkeypatch):
    started, release = threading.Event(), threading.Event()
    class DelayedClient(FakeClient):
        def request(self, operation, **options):
            if operation == "consumers":
                started.set()
                release.wait(3)
            return super().request(operation, **options)
    monkeypatch.setattr("jmsbrowser.state.WorkerClient", DelayedClient)
    FakeClient.release.set()
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    try:
        controller.connect()
        wait_until(controller, lambda: not controller.current.busy)
        state = controller.current
        state.client.calls.clear()
        controller.refresh_consumers()
        assert started.wait(1)
        controller.refresh_consumers()
        controller.select_queue(1)
        release.set()
        wait_until(controller, lambda: not state.busy)
        assert state.selected_queue.name == "B"
        assert state.consumer_snapshot is None and state.consumer_status == "idle"
        assert len([call for call in state.client.calls if call[0] == "consumers"]) == 1
        controller.refresh_consumers()
        wait_until(controller, lambda: state.consumer_status == "ready")
        assert state.consumer_snapshot.queue == "B"
    finally:
        release.set()
        controller.close()
