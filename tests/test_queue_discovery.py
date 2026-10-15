import json
import sys
from types import SimpleNamespace

import pytest

from jmsbrowser.brokers import ArtemisAdapter
from jmsbrowser.config import ConnectionConfig


PERMANENT_UUID = "orders.12345678-1234-1234-1234-123456789abc"
TEMPORARY_UUID = "87654321-4321-4321-4321-cba987654321"


@pytest.fixture
def artemis(monkeypatch):
    entries = {
        "orders": ("APP.ORDERS", 3, False),
        "uuid": (PERMANENT_UUID, 7, False),
        "temporary": (TEMPORARY_UUID, 2, True),
        "reply": ("APP.REPLY", 0, True),
        "ordinary": ("APP.NON_DURABLE", 1, False),
    }
    calls = []
    class ObjectName:
        def __init__(self, mbean):
            self.mbean = mbean
        def getKeyProperty(self, key):
            return json.dumps(entries[self.mbean][0]) if key == "queue" else '"anycast"'
        @staticmethod
        def unquote(value):
            return json.loads(value)
    def jclass(name):
        assert name == "javax.management.ObjectName"
        return ObjectName
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=jclass))
    def jolokia(payload):
        calls.append(payload)
        if isinstance(payload, dict):
            if payload["type"] == "search":
                return {"value": list(entries)}
            assert payload["attribute"] == "MessageCount"
            return {"value": entries[payload["mbean"]][1]}
        result = []
        for request in payload:
            assert request["type"] == "read"
            assert request["attribute"] == ["MessageCount", "Temporary"]
            _, depth, temporary = entries[request["mbean"]]
            result.append({"value": {"MessageCount": depth, "Temporary": temporary}})
        return result
    monkeypatch.setattr(ArtemisAdapter, "jolokia", lambda self, payload: jolokia(payload))
    return entries, calls


@pytest.mark.parametrize("options,expected", [
    ({}, ["APP.NON_DURABLE", "APP.ORDERS", PERMANENT_UUID]),
    ({"queue_pattern": "APP.*"}, ["APP.NON_DURABLE", "APP.ORDERS"]),
    ({"queues": (PERMANENT_UUID, TEMPORARY_UUID)}, [PERMANENT_UUID]),
    ({"queues": (TEMPORARY_UUID, "APP.REPLY")}, []),
])
def test_artemis_hides_temporary_queues_using_metadata_and_keeps_normal_uuid_names(artemis, options, expected):
    _, calls = artemis
    adapter = ArtemisAdapter(ConnectionConfig("Test", "artemis", **options))
    adapter.queue_mbeans = {"OLD.REPLY": "stale"}
    queues = adapter.list_queues()
    assert [queue.name for queue in queues] == expected
    assert set(adapter.queue_mbeans) == set(expected)
    assert all(queue.routing == "ANYCAST" for queue in queues)
    assert len(calls) == 2  # Fetch flags together with depths in the existing batch.
    if PERMANENT_UUID in expected:
        assert next(queue.depth for queue in queues if queue.name == PERMANENT_UUID) == 7
        assert adapter.queue_depth(PERMANENT_UUID) == 7


def test_artemis_empty_discovery_clears_cached_mbeans_without_a_metadata_request(artemis):
    entries, calls = artemis
    entries.clear()
    adapter = ArtemisAdapter(ConnectionConfig("Test", "artemis"))
    adapter.queue_mbeans = {"OLD.REPLY": "stale"}
    assert adapter.list_queues() == []
    assert adapter.queue_mbeans == {} and len(calls) == 1


def test_artemis_refresh_removes_a_queue_recreated_as_temporary(artemis):
    entries, _ = artemis
    adapter = ArtemisAdapter(ConnectionConfig("Test", "artemis"))
    assert "APP.ORDERS" in {queue.name for queue in adapter.list_queues()}
    entries["orders"] = ("APP.ORDERS", 0, True)
    assert "APP.ORDERS" not in {queue.name for queue in adapter.list_queues()}
    assert "APP.ORDERS" not in adapter.queue_mbeans
