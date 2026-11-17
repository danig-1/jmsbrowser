import io
import json
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

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
    calls, failures = [], {}
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
    def urlopen(request, **options):
        payload = json.loads(request.data)
        calls.append(payload)
        if isinstance(payload, dict):
            if payload["type"] == "search":
                result = {"status": 200, "value": list(entries)}
            else:
                assert payload["attribute"] == "MessageCount"
                result = {"status": 200, "value": entries[payload["mbean"]][1]}
        else:
            result = []
            for item in payload:
                assert item["type"] == "read"
                assert item["attribute"] == ["MessageCount", "Temporary", "ConsumerCount"]
                mbean = item["mbean"]
                if mbean in failures:
                    failure = failures[mbean]
                    if isinstance(failure, Exception):
                        raise failure
                    result.append(failure)
                else:
                    _, depth, temporary = entries[mbean]
                    result.append({"status": 200, "value": {"MessageCount": depth, "Temporary": temporary, "ConsumerCount": 4}})
        return io.BytesIO(json.dumps(result).encode("utf-8"))
    monkeypatch.setattr("jmsbrowser.brokers.urllib.request.urlopen", urlopen)
    return entries, calls, failures


def adapter_for_discovery(**options):
    return ArtemisAdapter(ConnectionConfig("Test", "artemis", management_url="http://localhost:8161/console/jolokia", **options))


@pytest.mark.parametrize("options,expected", [
    ({}, ["APP.NON_DURABLE", "APP.ORDERS", PERMANENT_UUID]),
    ({"queue_pattern": "APP.*"}, ["APP.NON_DURABLE", "APP.ORDERS"]),
    ({"queues": (PERMANENT_UUID, TEMPORARY_UUID)}, [PERMANENT_UUID]),
    ({"queues": (TEMPORARY_UUID, "APP.REPLY")}, []),
])
def test_artemis_hides_temporary_queues_using_metadata_and_keeps_normal_uuid_names(artemis, options, expected):
    _, calls, _ = artemis
    adapter = adapter_for_discovery(**options)
    adapter.queue_mbeans = {"OLD.REPLY": "stale"}
    queues = adapter.list_queues()
    assert [queue.name for queue in queues] == expected
    assert set(adapter.queue_mbeans) == set(expected)
    assert all(queue.routing == "ANYCAST" for queue in queues)
    assert all(queue.consumers == 4 for queue in queues)
    assert len(calls) == 2  # Fetch flags together with depths in the existing batch.
    if PERMANENT_UUID in expected:
        assert next(queue.depth for queue in queues if queue.name == PERMANENT_UUID) == 7
        assert adapter.queue_depth(PERMANENT_UUID) == 7


def test_artemis_empty_discovery_clears_cached_mbeans_without_a_metadata_request(artemis):
    entries, calls, _ = artemis
    entries.clear()
    adapter = adapter_for_discovery()
    adapter.queue_mbeans = {"OLD.REPLY": "stale"}
    assert adapter.list_queues() == []
    assert adapter.queue_mbeans == {} and len(calls) == 1


def test_artemis_refresh_removes_a_queue_recreated_as_temporary(artemis):
    entries, _, _ = artemis
    adapter = adapter_for_discovery()
    assert "APP.ORDERS" in {queue.name for queue in adapter.list_queues()}
    entries["orders"] = ("APP.ORDERS", 0, True)
    assert "APP.ORDERS" not in {queue.name for queue in adapter.list_queues()}
    assert "APP.ORDERS" not in adapter.queue_mbeans


@pytest.mark.parametrize("status,field", [(404, "error_type"), (400, "error_type_jmx")])
def test_artemis_skips_a_queue_removed_between_search_and_read(artemis, status, field):
    _, calls, failures = artemis
    failures["uuid"] = {"status": status, field: "javax.management.InstanceNotFoundException", "error": "Queue removed"}
    adapter = adapter_for_discovery()
    adapter.queue_mbeans = {PERMANENT_UUID: "uuid"}
    assert [(q.name, q.depth) for q in adapter.list_queues()] == [("APP.NON_DURABLE", 1), ("APP.ORDERS", 3)]
    assert adapter.queue_mbeans == {"APP.ORDERS": "orders", "APP.NON_DURABLE": "ordinary"}
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [
    {"status": 403, "error_type": "javax.management.InstanceNotFoundException", "error": "Access denied"},
    {"status": 404, "error_type": "javax.management.AttributeNotFoundException", "error": "Attribute missing"},
    HTTPError("http://localhost:8161/console/jolokia", 404, "Endpoint missing", {}, None),
])
def test_artemis_still_surfaces_other_management_failures(artemis, failure):
    _, _, failures = artemis
    adapter = adapter_for_discovery()
    adapter.list_queues()
    original = adapter.queue_mbeans.copy()
    failures["uuid"] = failure
    if isinstance(failure, HTTPError):
        with pytest.raises(HTTPError, match="Endpoint missing"):
            adapter.list_queues()
    else:
        with pytest.raises(RuntimeError, match=failure["error"]):
            adapter.list_queues()
    assert adapter.queue_mbeans == original
