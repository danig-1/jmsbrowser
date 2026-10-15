"""Exercise application sends on new disposable queues; preserve all fixtures."""
from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import pytest

from jmsbrowser.client import BrokerError, WorkerClient
from jmsbrowser.config import load_config
from jmsbrowser.outgoing import OutgoingMessage, OutgoingProperty

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("JMSBROWSER_INTEGRATION") != "1", reason="Set JMSBROWSER_INTEGRATION=1 with local brokers running")]


def command(args, text=None):
    result = subprocess.run(["docker", "compose", "exec", "-T", *args], input=text, cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(params=[0, 1], ids=["artemis", "ibm"])
def destination_queue(request, monkeypatch):
    monkeypatch.setenv("ARTEMIS_PASSWORD", "artemis-local-123")
    monkeypatch.setenv("MQ_APP_PASSWORD", "app-local-123")
    index = request.param
    name = "DEV.COMPOSER.TEST." + uuid.uuid4().hex[:12].upper()
    assert re.fullmatch(r"DEV\.COMPOSER\.TEST\.[A-F0-9]{12}", name)
    options = ["--name", name, "--url", "tcp://localhost:61616", "--user", "artemis", "--password", "artemis-local-123", "--silent"]
    if index == 0:
        output = command(["artemis", "/var/lib/artemis-instance/bin/artemis", "queue", "create", *options, "--address", name, "--anycast", "--durable", "--auto-create-address"])
        assert "created" in output.lower(), output
    else:
        output = command(["ibm", "runmqsc", "QM1"], f"DEFINE QLOCAL('{name}') DEFPSIST(YES)\n")
        assert "AMQ8006I" in output, output
    try:
        connection = replace(load_config(ROOT / "config.example.yaml").connections[index], timeout_seconds=30)
        yield connection, name
    finally:
        # Only this fixture's exact fresh UUID queue is removed, after all handles close.
        if index == 0:
            output = command(["artemis", "/var/lib/artemis-instance/bin/artemis", "queue", "delete", *options, "--autoDeleteAddress"])
            assert "deleted" in output.lower(), output
        else:
            output = command(["ibm", "runmqsc", "QM1"], f"DELETE QLOCAL('{name}') PURGE\n")
            assert "AMQ8007I" in output, output


def test_real_send_options_properties_browse_and_expiry(destination_queue):
    connection, name = destination_queue
    properties = [OutgoingProperty("text", "String", "héllo 🙂"), OutgoingProperty("flag", "Boolean", "true"),
                  OutgoingProperty("byteValue", "Byte", "-128"), OutgoingProperty("shortValue", "Short", "32767"),
                  OutgoingProperty("intValue", "Int", "2147483647"), OutgoingProperty("longValue", "Long", "9223372036854775807"),
                  OutgoingProperty("floatValue", "Float", "1.25"), OutgoingProperty("doubleValue", "Double", "2.5")]
    draft = OutgoingMessage("Hello é🙂\nSecond line", 7, True, 0, "composer.integration", "request-composer-123", properties)
    with WorkerClient(connection) as client:
        before = {queue["name"]: queue["depth"] for queue in client.request("connect")}
        assert before[name] == 0
        receipt = client.request("send", queue=name, message=draft.to_dict())
        assert receipt["queue"] == name and receipt["JMSMessageID"].startswith("ID:")
        assert receipt["size"] == len(draft.body.encode("utf-8"))
        first = client.request("browse", queue=name, limit=100, max_body_bytes=1024)
        assert len(first) == 1
        message = first[0]
        assert message["body"] == draft.body and message["message_id"] == receipt["JMSMessageID"]
        assert message["priority"] == 7 and message["headers"]["JMSDeliveryMode"] == 2
        assert message["headers"]["JMSType"] == draft.jms_type and message["headers"]["JMSCorrelationID"] == draft.correlation_id
        assert message["headers"]["JMSTimestamp"] == receipt["JMSTimestamp"] and message["headers"]["JMSExpiration"] == 0
        assert {key: message["properties"][key] for key, _, _ in draft.validate()} == {key: value for key, _, value in draft.validate()}
        assert client.request("browse", queue=name, limit=100, max_body_bytes=1024) == first
        assert client.request("queue_depth", queue=name)["depth"] == 1
        expiring = OutgoingMessage("expires", 1, False, 2000)
        expiry_receipt = client.request("send", queue=name, message=expiring.to_dict())
        after = client.request("browse", queue=name, limit=100, max_body_bytes=1024)
        short_lived = next(item for item in after if item["message_id"] == expiry_receipt["JMSMessageID"])
        assert short_lived["headers"]["JMSDeliveryMode"] == 1 and short_lived["priority"] == 1
        assert short_lived["headers"]["JMSExpiration"] - short_lived["headers"]["JMSTimestamp"] == 2000
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            remaining = client.request("browse", queue=name, limit=100, max_body_bytes=1024)
            if len(remaining) == 1:
                break
            time.sleep(0.1)
        assert remaining == first
        invalid = draft.to_dict() | {"priority": 99}
        with pytest.raises(BrokerError) as error:
            client.request("send", queue=name, message=invalid)
        assert error.value.outcome == "not_sent"
        with pytest.raises(BrokerError) as error:
            client.request("send", queue="DEV.DOES.NOT.EXIST", message=draft.to_dict())
        assert error.value.outcome == "not_sent"
        final = {queue["name"]: queue["depth"] for queue in client.request("queues")}
        assert {key: depth for key, depth in final.items() if key != name} == {key: depth for key, depth in before.items() if key != name}
