import queue
from types import SimpleNamespace
import threading
import time

import pytest

from jmsbrowser.brokers import bounded_text
from jmsbrowser.client import BrokerError, WorkerClient
from jmsbrowser.config import ConnectionConfig
from jmsbrowser.domain import Message, format_size


def test_utf8_payload_size_and_truncation_preserve_complete_characters():
    body, size, truncated = bounded_text("é🙂abc", 5)
    assert body == "é" and size == 9 and truncated
    body, size, truncated = bounded_text("é🙂abc", 9)
    assert body == "é🙂abc" and size == 9 and not truncated


def test_message_details_preserve_raw_body_and_show_metadata():
    message = Message("ID:one", "TextMessage", 9, '{"a":1}', 7, {"JMSPriority": 9}, {"source": "test"})
    assert message.body == '{"a":1}'
    assert '\n  "a": 1\n' in message.detail()
    assert 'JMSPriority │ 9' in message.detail(1)
    assert 'source │ test' in message.detail(2)
    assert Message.from_dict(message.to_dict()) == message
    assert format_size(None) == "n/a"


def test_worker_deadline_kills_unresponsive_process():
    class Process:
        killed = False
        stdin = SimpleNamespace(write=lambda _: None, flush=lambda: None)
        def poll(self):
            return None
        def kill(self):
            self.killed = True

    client = WorkerClient.__new__(WorkerClient)
    client.config = SimpleNamespace(timeout_seconds=0.02, name="Unreachable")
    client.closed, client.lock, client.responses = False, threading.Lock(), queue.Queue()
    client.process = Process()
    started = time.monotonic()
    with pytest.raises(BrokerError, match="timed out"):
        client.request("queues")
    assert client.process.killed and client.closed
    assert time.monotonic() - started < 1


def test_source_worker_browses_when_parent_runs_outside_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    with WorkerClient(ConnectionConfig("Offline Artemis", "artemis"), demo=True) as client:
        queues = client.request("connect")
        assert any(queue["name"] == "DEV.ORDERS" and queue["depth"] == 3 for queue in queues)
        first = client.request("browse", queue="DEV.ORDERS", limit=100, max_body_bytes=262144)
        second = client.request("browse", queue="DEV.ORDERS", limit=100, max_body_bytes=262144)
        assert len(first) == 3
        assert first == second


def test_worker_full_message_reads_past_preview_limit_without_consuming():
    with WorkerClient(ConnectionConfig("Demo", "artemis"), demo=True) as client:
        before = client.request("connect")
        preview = client.request("browse", queue="DEV.ORDERS", limit=100, max_body_bytes=5)
        assert preview[0]["truncated"]
        full = client.request("full_message", queue="DEV.ORDERS", message_id=preview[0]["message_id"])
        assert not full["truncated"] and full["body"].startswith(preview[0]["body"])
        assert len(full["body"].encode()) == full["size"] > 5
        assert before == client.request("queues")
        with pytest.raises(BrokerError, match="no longer"):
            client.request("full_message", queue="DEV.ORDERS", message_id="ID:gone")
        assert before == client.request("queues")
