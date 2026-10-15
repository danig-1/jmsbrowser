import sys
from types import SimpleNamespace
import pytest
from jmsbrowser.brokers import JMSAdapter, extract_message
from jmsbrowser.config import ConnectionConfig


class Enumeration:
    def __init__(self, values):
        self.values = iter(values)
        self.next = next(self.values, None)
    def hasMoreElements(self):
        return self.next is not None
    def nextElement(self):
        result = self.next
        self.next = next(self.values, None)
        return result


class Base:
    def __getattr__(self, name):
        values = {"JMSMessageID": "ID:test", "JMSType": None, "JMSPriority": 7, "JMSTimestamp": 0,
                  "JMSExpiration": 0, "JMSDeliveryMode": 2, "JMSRedelivered": False,
                  "JMSCorrelationID": "", "JMSReplyTo": None, "JMSDestination": "TEST"}
        if name.startswith("get") and name[3:] in values:
            return lambda: values[name[3:]]
        raise AttributeError(name)
    def getPropertyNames(self):
        return Enumeration(["source"])
    def getObjectProperty(self, name):
        return "test"


class Text(Base):
    def getText(self):
        return "é🙂abc"


class Bytes(Base):
    raw = b"\x00\x01\x7f\x80\xfe\xff"
    def getBodyLength(self):
        return len(self.raw)
    def reset(self):
        self.offset = 0
    def readBytes(self, buffer):
        chunk = self.raw[self.offset:self.offset + min(2, len(buffer))]
        buffer[:len(chunk)] = [b if b < 128 else b - 256 for b in chunk]
        self.offset += len(chunk)
        return len(chunk)


class Map(Base):
    def getMapNames(self):
        return Enumeration(["name", "count"])
    def getObject(self, name):
        return {"name": "Ada", "count": 3}[name]


class EOF(Exception):
    pass


class Stream(Base):
    def reset(self):
        self.values = iter(["hello", 42, True])
    def readObject(self):
        try:
            return next(self.values)
        except StopIteration:
            raise EOF from None


class Object(Base):
    def getObject(self):
        raise AssertionError("Object messages must never be deserialized")


@pytest.fixture(autouse=True)
def jpype_stub(monkeypatch):
    classes = {"TextMessage": Text, "BytesMessage": Bytes, "MapMessage": Map, "StreamMessage": Stream,
               "ObjectMessage": Object, "MessageEOFException": EOF}
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda name: classes[name.split(".")[-1]],
                                                            JByte=int, JArray=lambda kind: lambda size: [0] * size))


def test_text_metadata_priority_and_utf8_size():
    message = extract_message(Text(), 5)
    assert (message.type, message.priority, message.body, message.size, message.truncated) == ("TextMessage", 7, "é", 9, True)
    assert message.headers["JMSType"] is None and message.properties == {"source": "test"}


def test_bytes_preserve_high_bytes_partial_reads_and_true_size():
    message = extract_message(Bytes(), 5)
    assert message.type == "BytesMessage" and message.size == 6 and message.truncated
    assert "00 01 7f 80 fe" in message.body


@pytest.mark.parametrize("source,kind", [(Map(), "MapMessage"), (Stream(), "StreamMessage"), (Object(), "ObjectMessage")])
def test_unknown_encoded_sizes_are_honest_and_object_is_not_deserialized(source, kind):
    message = extract_message(source, 1024)
    assert message.type == kind and message.size is None
    if kind == "MapMessage":
        assert '"name": "Ada"' in message.body
    elif kind == "StreamMessage":
        assert "hello" in message.body and "42" in message.body
    else:
        assert "not deserialized" in message.body


@pytest.mark.parametrize("source,expected", [(Text(), "é🙂abc"), (Bytes(), "00 01 7f 80 fe ff"),
                                             (Map(), '"count": 3'), (Stream(), 'true'), (Object(), "not deserialized")])
def test_unbounded_copy_read_is_complete_for_each_supported_body_type(source, expected):
    full = extract_message(source, None)
    assert expected in full.body and not full.truncated


def test_full_message_uses_escaped_id_selector_and_closes_browser_on_success_and_error():
    class Browser:
        closed = False
        values = [Text()]
        def getEnumeration(self):
            return Enumeration(self.values)
        def close(self):
            self.closed = True

    browser, calls = Browser(), []
    adapter = JMSAdapter(ConnectionConfig("test", "artemis"))
    adapter.session = SimpleNamespace(createQueue=lambda name: name,
                                      createBrowser=lambda *args: (calls.append(args), browser)[1])
    message = adapter.full_message("TEST", "ID:test")
    assert message.body == "é🙂abc" and not message.truncated and browser.closed
    assert calls == [("TEST", "JMSMessageID = 'ID:test'")]
    browser.closed = False
    browser.values = []
    with pytest.raises(ValueError, match="no longer"):
        adapter.full_message("TEST", "ID:one'two")
    assert calls[-1] == ("TEST", "JMSMessageID = 'ID:one''two'") and browser.closed
    with pytest.raises(ValueError, match="no MessageId"):
        adapter.full_message("TEST", "")
    browser.values = [Text()]
    with pytest.raises(ValueError, match="different MessageId"):
        adapter.full_message("TEST", "ID:other")
    assert browser.closed


def test_map_and_stream_copy_include_large_binary_fields_and_all_later_values():
    class JavaBytes(list):
        def getClass(self):
            return SimpleNamespace(getName=lambda: "[B")
    binary = JavaBytes([1, 127, -1] * 5000)
    class BinaryMap(Map):
        def getObject(self, name):
            return binary if name == "name" else "last field"
    class BinaryStream(Stream):
        def reset(self):
            self.values = iter([binary, "last field"])
    for source in (BinaryMap(), BinaryStream()):
        preview = extract_message(source, 20000)
        assert preview.truncated
        full = extract_message(source, None)
        assert not full.truncated and "last field" in full.body
        assert "017fff" * 5000 in full.body
