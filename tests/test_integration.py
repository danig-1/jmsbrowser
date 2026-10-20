from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from jmsbrowser.client import WorkerClient
from jmsbrowser.config import load_config

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("JMSBROWSER_INTEGRATION") != "1", reason="Set JMSBROWSER_INTEGRATION=1 with local brokers running")]


@pytest.mark.parametrize("index", [0, 1], ids=["artemis", "ibm"])
def test_actual_application_browses_every_fixture_without_consuming(index):
    connection = replace(load_config(ROOT / "config.example.yaml").connections[index], timeout_seconds=30)
    expected = {"DEV.EMPTY": 0, "DEV.NOTIFICATIONS": 1, "DEV.ORDERS": 3, "DEV.PAYMENTS": 2}
    with WorkerClient(connection) as client:
        before = {q["name"]: q["depth"] for q in client.request("connect")}
        assert not any(name.startswith("DEV.JMSBROWSER.") for name in before)
        assert {name: before[name] for name in expected} == expected
        for name, count in expected.items():
            assert client.request("queue_depth", queue=name) == {"name": name, "depth": count, "consumers": 0}
            first = client.request("browse", queue=name, limit=100, max_body_bytes=262144)
            second = client.request("browse", queue=name, limit=100, max_body_bytes=262144)
            assert first == second and len(first) == count
            fixtures = [json.loads(file.read_text()) for file in sorted((ROOT / "dev/messages" / name).glob("*.json"))]
            assert [json.loads(m["body"]) for m in first] == fixtures
            for message in first:
                assert message["type"] == "TextMessage"
                assert message["priority"] == (4 if index == 0 else 0)
                assert message["size"] == len(message["body"].encode("utf-8"))
                assert message["message_id"].startswith("ID:")
        limited = client.request("browse", queue="DEV.ORDERS", limit=2, max_body_bytes=20)
        assert len(limited) == 2 and all(m["truncated"] and m["size"] > 20 for m in limited)
        after = {q["name"]: q["depth"] for q in client.request("queues")}
        assert before == after


@pytest.mark.parametrize("index", [0, 1], ids=["artemis", "ibm"])
def test_full_message_copy_read_preserves_queue_depth_and_ids(index):
    connection = replace(load_config(ROOT / "config.example.yaml").connections[index], timeout_seconds=30)
    with WorkerClient(connection) as client:
        client.request("connect")
        before = client.request("queue_depth", queue="DEV.ORDERS")
        first = client.request("browse", queue="DEV.ORDERS", limit=100, max_body_bytes=10)
        assert first and first[0]["truncated"]
        full = client.request("full_message", queue="DEV.ORDERS", message_id=first[0]["message_id"])
        assert not full["truncated"] and len(full["body"].encode()) == full["size"] > 10
        assert full["headers"] == first[0]["headers"] and full["properties"] == first[0]["properties"]
        assert client.request("queue_depth", queue="DEV.ORDERS") == before
        assert client.request("browse", queue="DEV.ORDERS", limit=100, max_body_bytes=10) == first


def test_artemis_loadtest_has_1000_persistent_messages_without_consuming():
    config = load_config(ROOT / "config.example.yaml")
    connection = replace(config.connections[0], timeout_seconds=30)
    body = json.loads((ROOT / "dev/loadtest-message.json").read_text())
    with WorkerClient(connection) as client:
        queues = {q["name"]: q["depth"] for q in client.request("connect")}
        assert queues["DEV.LOADTEST"] == 1000
        limited = client.request("browse", queue="DEV.LOADTEST", limit=100, max_body_bytes=1024)
        assert len(limited) == 100
        first = client.request("browse", queue="DEV.LOADTEST", limit=1000, max_body_bytes=1024)
        second = client.request("browse", queue="DEV.LOADTEST", limit=1000, max_body_bytes=1024)
        assert first == second and len(first) == 1000
        assert len({m["message_id"] for m in first}) == 1000
        assert [m["properties"]["count"] for m in first] == list(range(1000))
        assert all(m["headers"]["JMSDeliveryMode"] == 2 and json.loads(m["body"]) == body for m in first)
        assert client.request("queue_depth", queue="DEV.LOADTEST")["depth"] == 1000


@pytest.mark.parametrize("index", [0, 1], ids=["artemis", "ibm"])
def test_consumer_inquiry_detects_input_only_and_does_not_consume(index):
    connection = replace(load_config(ROOT / "config.example.yaml").connections[index], timeout_seconds=30)
    with WorkerClient(connection) as client:
        before = client.request("connect")
        empty = client.request("consumers", queue="DEV.EMPTY")
        assert empty["consumers"] == []
        fixture = subprocess.Popen([sys.executable, "-u", "-m", "tests.consumer_fixture", str(index)],
                                   cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            reader = ThreadPoolExecutor(max_workers=1)
            ready = reader.submit(fixture.stdout.readline)
            assert ready.result(timeout=15).strip() == "READY"
            snapshot = client.request("consumers", queue="DEV.EMPTY")
            assert snapshot["queue"] == "DEV.EMPTY"
            assert len(snapshot["consumers"]) == 1  # the held browser is excluded
            consumer = snapshot["consumers"][0]
            assert consumer["user"] == ("artemis" if index == 0 else "app")
            assert consumer["remote_address"]
            assert client.request("queue_depth", queue="DEV.EMPTY")["consumers"] == 1
            assert next(q for q in client.request("queues") if q["name"] == "DEV.EMPTY")["consumers"] == 1
            if index == 0:
                assert snapshot["unit"] == "consumers"
                assert consumer["application"] == "jmsbrowser-integration-consumer"
                assert consumer["protocol"] == "CORE"
                assert consumer["selector"] == "JMSCorrelationID = 'jmsbrowser-test-no-match'"
                assert consumer["consumer_id"] and consumer["connection_id"] and consumer["session_id"]
                assert consumer["consumer_count"] == 2  # input consumer + browser on the same session
                assert consumer["producer_count"] == 1  # held producer never sends
                assert consumer["creation_time"]
                assert not snapshot["note"]
            else:
                assert snapshot["unit"] == "input handles"
                assert consumer["details"]["Channel"] == "DEV.APP.SVRCONN"
                assert consumer["details"]["ProcessId"] > 0
            assert client.request("consumers", queue="DEV.ORDERS")["consumers"] == []
            assert client.request("browse", queue="DEV.EMPTY", limit=100, max_body_bytes=1024) == []
        finally:
            try:
                _, error = fixture.communicate("\n", timeout=10)
            except subprocess.TimeoutExpired:
                fixture.kill()
                _, error = fixture.communicate()
            reader.shutdown(wait=True)
            assert fixture.returncode == 0, error
        assert client.request("consumers", queue="DEV.EMPTY")["consumers"] == []
        assert client.request("queues") == before
