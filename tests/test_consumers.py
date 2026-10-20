import json
import sys
from types import SimpleNamespace

import pytest

from jmsbrowser.brokers import ArtemisAdapter, IBMAdapter
from jmsbrowser.config import ConnectionConfig


def test_artemis_hides_browser_and_enriches_by_global_consumer_id(monkeypatch):
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda _: lambda _: SimpleNamespace(getKeyProperty=lambda _: '"broker"')))
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-mbean"}
    calls = []
    def jolokia(request):
        calls.append(request)
        if isinstance(request, list):
            return [{"value": json.dumps({"data": [{"id": "sess", "connectionID": "conn", "user": "session-user",
                                                     "consumerCount": 7, "producerCount": 0,
                                                     "creationTime": "session created earlier"}]})}]
        if request["mbean"] == "queue-mbean":
            return {"value": json.dumps([{"consumerID": 0, "sequentialId": 10, "browseOnly": False, "connectionID": "conn", "sessionID": "sess"},
                                         {"consumerID": 1, "sequentialId": 11, "browseOnly": True}])}
        return {"value": json.dumps({"data": [{"id": "10", "queue": "A", "clientID": "worker", "remoteAddress": "host:123", "user": "consumer-view-user", "protocol": "AMQP", "filter": "x = 1", "creationTime": "consumer created later"}]})}
    adapter.jolokia = jolokia
    result = adapter.consumers("A")
    assert len(result.consumers) == 1
    item = result.consumers[0]
    assert (item.consumer_id, item.application, item.remote_address, item.selector) == ("10", "worker", "host:123", "x = 1")
    assert (item.connection_id, item.session_id) == ("conn", "sess")
    assert (item.user, item.consumer_count, item.producer_count) == ("session-user", 7, 0)
    assert item.creation_time == "consumer created later"
    assert item.details["Session creation time"] == "session created earlier"
    assert not result.note
    assert json.loads(calls[1]["arguments"][0])["value"] == "A"
    assert calls[2][0]["operation"] == "listSessions(java.lang.String,int,int)"
    assert json.loads(calls[2][0]["arguments"][0]) == {"field": "connectionID", "operation": "EQUALS", "value": "conn"}


def test_artemis_session_join_is_per_connection_and_shared_session_counts_are_not_queue_counts(monkeypatch):
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda _: lambda _: SimpleNamespace(getKeyProperty=lambda _: '"broker"')))
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-mbean"}
    attached = [dict(consumerID=i, sequentialId=i, browseOnly=False, connectionID=connection, sessionID=session)
                for i, connection, session in [(1, "c1", "s1"), (2, "c1", "s1"), (3, "c1", "s2"), (4, "c2", "s1")]]
    attached.append(dict(consumerID=5, sequentialId=5, browseOnly=True, connectionID="browser-only", sessionID="browser"))
    session_calls = []
    def jolokia(request):
        if isinstance(request, list):
            session_calls.extend(request)
            return [{"value": json.dumps({"data": sessions})} for sessions in [
                [{"id": "s1", "connectionID": "c1", "user": "u1", "consumerCount": 12, "producerCount": 4},
                 {"id": "s2", "connectionID": "c1", "user": "u2", "consumerCount": "3", "producerCount": "0"},
                 {"id": "s1", "connectionID": "wrong-connection", "user": "wrong", "consumerCount": 999}],
                [{"id": "s1", "connectionID": "c2", "user": "u3", "consumerCount": 8, "producerCount": 6}]]]
        if request["mbean"] == "queue-mbean":
            return {"value": json.dumps(attached)}
        return {"value": '{"data": []}'}
    adapter.jolokia = jolokia
    snapshot = adapter.consumers("A")
    assert [(c.user, c.consumer_count, c.producer_count) for c in snapshot.consumers] == [
        ("u1", 12, 4), ("u1", 12, 4), ("u2", 3, 0), ("u3", 8, 6)]
    assert len(session_calls) == 2
    assert [json.loads(call["arguments"][0])["value"] for call in session_calls] == ["c1", "c2"]
    from jmsbrowser.domain import ConsumerSnapshot
    assert ConsumerSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize("denied", ["sessions", "consumers"])
def test_artemis_partial_permissions_preserve_queue_rows_and_independent_metadata(monkeypatch, denied):
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda _: lambda _: SimpleNamespace(getKeyProperty=lambda _: '"broker"')))
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-mbean"}
    def jolokia(request):
        if isinstance(request, list):
            if denied == "sessions":
                raise RuntimeError("Session access denied")
            return [{"value": '{"data": [{"id":"s", "connectionID":"c", "user":"session", "consumerCount":5, "producerCount":0}]}'}]
        if request["mbean"] == "queue-mbean":
            return {"value": '[{"sequentialId":1,"browseOnly":false,"connectionID":"c","sessionID":"s"}]'}
        if denied == "consumers":
            raise RuntimeError("Consumer access denied")
        return {"value": '{"data": [{"id":"1", "queue":"A", "user":"do not substitute", "remoteAddress":"host:123", "creationTime":"Tue Oct 20 10:00:00 UTC 2026"}]}'}
    adapter.jolokia = jolokia
    snapshot = adapter.consumers("A")
    assert len(snapshot.consumers) == 1
    consumer = snapshot.consumers[0]
    if denied == "sessions":
        assert not consumer.user and consumer.consumer_count is None and consumer.producer_count is None
        assert consumer.remote_address == "host:123" and consumer.creation_time == "Tue Oct 20 10:00:00 UTC 2026"
        assert "Session details unavailable" in snapshot.note
    else:
        assert (consumer.user, consumer.consumer_count, consumer.producer_count) == ("session", 5, 0)
        assert not consumer.remote_address and consumer.creation_time is None
        assert "Connection details unavailable" in snapshot.note


def test_artemis_empty_queue_skips_server_metadata_requests():
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-mbean"}
    calls = []
    adapter.jolokia = lambda request: (calls.append(request), {"value": "[]"})[1]
    assert not adapter.consumers("A").consumers and len(calls) == 1


def test_artemis_connection_detail_permissions_preserve_authoritative_consumer_count(monkeypatch):
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda _: lambda _: SimpleNamespace(getKeyProperty=lambda _: '"broker"')))
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-mbean"}
    def jolokia(request):
        if request["mbean"] == "queue-mbean":
            return {"value": '[{"consumerID":0,"sequentialId":10,"browseOnly":false}]'}
        raise RuntimeError("Access denied")
    adapter.jolokia = jolokia
    result = adapter.consumers("A")
    assert len(result.consumers) == 1 and not result.consumers[0].application
    assert "Access denied" in result.note


@pytest.mark.parametrize("reason,empty", [(4091, True), (2035, False)])
def test_ibm_distinguishes_no_input_handles_from_permissions(monkeypatch, reason, empty):
    class PCFException(Exception):
        reasonCode = reason
    class Request:
        def __init__(self, _):
            self.parameters = {}
        def addParameter(self, key, value):
            self.parameters[key] = value
    names = "MQCMD_INQUIRE_Q_STATUS MQIACF_Q_STATUS_TYPE MQIACF_Q_HANDLE MQIACF_OPEN_TYPE MQQSOT_INPUT".split()
    constants = SimpleNamespace(**{name: name for name in names}, MQRCCF_Q_STATUS_NOT_FOUND=4091)
    classes = {"com.ibm.mq.constants.CMQC": SimpleNamespace(MQCA_Q_NAME="QName"), "com.ibm.mq.constants.CMQCFC": constants,
               "com.ibm.mq.headers.pcf.PCFMessage": Request, "com.ibm.mq.headers.pcf.PCFException": PCFException}
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda name: classes[name]))
    adapter = IBMAdapter(ConnectionConfig("IBM", "ibm"))
    def send(request):
        assert request.parameters == {"QName": "A", "MQIACF_Q_STATUS_TYPE": "MQIACF_Q_HANDLE", "MQIACF_OPEN_TYPE": "MQQSOT_INPUT"}
        raise PCFException("inquiry failed")
    adapter.pcf_agent = SimpleNamespace(send=send)
    if empty:
        result = adapter.consumers("A")
        assert result.consumers == [] and result.unit == "input handles"
    else:
        with pytest.raises(PCFException):
            adapter.consumers("A")
