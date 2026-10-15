import sys
from types import SimpleNamespace

from jmsbrowser.brokers import IBMAdapter
from jmsbrowser.config import ConnectionConfig


def test_ibm_depth_inquiries_reuse_connection_and_close_queue_handles(monkeypatch):
    created, opened, closed = [], [], []
    class Hashtable(dict):
        def put(self, key, value):
            self[key] = value
    class Manager:
        connected = True
        def __init__(self, *args):
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
    adapter = IBMAdapter(ConnectionConfig("IBM", "ibm", username="app", queue_manager="QM1"))
    assert adapter.queue_depth("A") == 42
    assert adapter.queue_depth("B") == 42
    assert len(created) == 1 and opened == closed == ["A", "B"]
    assert created[0].connected
    adapter.close()
    assert not created[0].connected
