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
        if request["mbean"] == "queue-mbean":
            return {"value": json.dumps([{"consumerID": 0, "sequentialId": 10, "browseOnly": False, "connectionID": "conn", "sessionID": "sess"},
                                         {"consumerID": 1, "sequentialId": 11, "browseOnly": True}])}
        return {"value": json.dumps({"data": [{"id": "10", "queue": "A", "clientID": "worker", "remoteAddress": "host:123", "user": "app", "protocol": "AMQP", "filter": "x = 1"}]})}
    adapter.jolokia = jolokia
    result = adapter.consumers("A")
    assert len(result.consumers) == 1
    item = result.consumers[0]
    assert (item.consumer_id, item.application, item.remote_address, item.selector) == ("10", "worker", "host:123", "x = 1")
    assert (item.connection_id, item.session_id) == ("conn", "sess")
    assert json.loads(calls[1]["arguments"][0])["value"] == "A"


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
