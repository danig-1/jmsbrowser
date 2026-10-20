import sys
from types import SimpleNamespace

import pytest

from jmsbrowser.brokers import ArtemisAdapter, IBMAdapter
from jmsbrowser.config import ConnectionConfig


def test_ibm_depth_inquiries_reuse_connection_and_close_queue_handles(monkeypatch):
    created, opened, closed = [], [], []
    class Hashtable(dict):
        def put(self, key, value):
            self[key] = value
    class Manager:
        connected = True
        def __init__(self, *args):
            self.properties = args[1]
            created.append(self)
        def isConnected(self):
            return self.connected
        def accessQueue(self, name, options):
            opened.append(name)
            return SimpleNamespace(getCurrentDepth=lambda: 42, close=lambda: closed.append(name))
        def disconnect(self):
            self.connected = False
    fields = "HOST_NAME_PROPERTY CHANNEL_PROPERTY USER_ID_PROPERTY PASSWORD_PROPERTY PORT_PROPERTY TRANSPORT_PROPERTY TRANSPORT_MQSERIES_CLIENT USE_MQCSP_AUTHENTICATION_PROPERTY".split()
    constants = SimpleNamespace(**{name: name for name in fields}, MQOO_INQUIRE=32, MQOO_FAIL_IF_QUIESCING=8192)
    classes = {"com.ibm.mq.constants.CMQC": constants, "java.util.Hashtable": Hashtable,
               "java.lang.Integer": int, "java.lang.Boolean": bool, "com.ibm.mq.MQQueueManager": Manager}
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda name: classes[name]))
    adapter = IBMAdapter(ConnectionConfig("IBM", "ibm", username="app", queue_manager="QM1", password=12345678))
    assert adapter.queue_depth("A") == 42
    assert adapter.queue_depth("B") == 42
    assert len(created) == 1 and opened == closed == ["A", "B"]
    assert created[0].properties[constants.PASSWORD_PROPERTY] == "12345678"
    assert created[0].connected
    adapter.close()
    assert not created[0].connected


def test_artemis_single_queue_stats_read_depth_and_consumers_together():
    adapter = ArtemisAdapter(ConnectionConfig("Artemis", "artemis"))
    adapter.queue_mbeans = {"A": "queue-A"}
    calls = []
    adapter.jolokia = lambda request: (calls.append(request), {"value": {"MessageCount": 42, "ConsumerCount": 0}})[1]
    assert adapter.queue_stats("A") == {"name": "A", "depth": 42, "consumers": 0}
    assert calls == [{"type": "read", "mbean": "queue-A", "attribute": ["MessageCount", "ConsumerCount"]}]


def test_ibm_queue_stats_and_explicit_queue_list_use_input_handles_and_close_inquiries(monkeypatch):
    closed, opened = [], []
    constants = SimpleNamespace(MQOO_INQUIRE=32, MQOO_FAIL_IF_QUIESCING=8192)
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda name: constants))
    def access(name, options):
        opened.append((name, options))
        return SimpleNamespace(getCurrentDepth=lambda: 42, getOpenInputCount=lambda: 3, close=lambda: closed.append(name))
    adapter = IBMAdapter(ConnectionConfig("IBM", "ibm", queues=("A",)))
    monkeypatch.setattr(adapter, "queue_manager_connection", lambda: SimpleNamespace(accessQueue=access))
    assert adapter.queue_stats("A") == {"name": "A", "depth": 42, "consumers": 3}
    queue = adapter.list_queues()[0]
    assert (queue.depth, queue.consumers) == (42, 3)
    assert closed == ["A", "A"] and opened == [("A", 8224), ("A", 8224)]


@pytest.mark.parametrize("reason", [0, 2035, 2059], ids=["all-permitted", "mixed-permissions", "other-error"])
def test_ibm_default_discovery_includes_non_dev_queues_and_handles_pcf_errors(monkeypatch, reason):
    parameters = {}
    class Request:
        def __init__(self, command):
            pass
        def addParameter(self, key, value):
            parameters[key] = value
    def response(name="", error=0):
        def queue_name(_):
            assert not error, "Error replies do not contain queue attributes."
            return name
        return SimpleNamespace(getReason=lambda: error, getCompCode=lambda: 2 if error else 0,
                               getStringParameterValue=queue_name, getIntParameterValue=lambda _: 7)
    replies = [response("DEV.ORDERS"), response("APP.ORDERS"), response("DEV.JMSBROWSER.REPLY")]
    if reason:
        replies.insert(1, response(error=reason))
    constants = SimpleNamespace(MQCA_Q_NAME="name", MQIA_Q_TYPE="type", MQQT_LOCAL=1,
                                MQIA_CURRENT_Q_DEPTH="depth", MQIA_OPEN_INPUT_COUNT="consumers", MQRC_NOT_AUTHORIZED=2035, MQCC_FAILED=2)
    formats = SimpleNamespace(MQCMD_INQUIRE_Q="inquire", MQIACF_Q_ATTRS="attrs")
    classes = {"com.ibm.mq.constants.CMQC": constants, "com.ibm.mq.constants.CMQCFC": formats,
               "com.ibm.mq.headers.pcf.PCFMessage": Request}
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda name: classes[name],
                                                             JArray=lambda _: list, JInt=int))
    adapter = IBMAdapter(ConnectionConfig("IBM", "ibm", reply_prefix="DEV.JMSBROWSER.*"))
    monkeypatch.setattr(adapter, "queue_manager_connection", lambda: object())
    monkeypatch.setattr(adapter, "inquiry_agent", lambda: SimpleNamespace(
        getReplyQueueName=lambda: "DEV.JMSBROWSER.REPLY", send=lambda _: replies))
    if reason not in (0, 2035):
        with pytest.raises(RuntimeError, match=f"reason {reason}"):
            adapter.list_queues()
    else:
        queues = adapter.list_queues()
        assert [(q.name, q.depth) for q in queues] == [("APP.ORDERS", 7), ("DEV.ORDERS", 7)]
    assert parameters[constants.MQCA_Q_NAME] == "*"
