import base64
import io
import json
import sys
from types import SimpleNamespace

import pytest
import yaml

from jmsbrowser.brokers import ArtemisAdapter, JMSAdapter
from jmsbrowser.config import ConfigError, ConnectionConfig, load_config
from jmsbrowser.outgoing import destination
from jmsbrowser import runtime


FIELDS = ("password", "management_password", "tls.trust_store_password", "tls.key_store_password")


def read_config(tmp_path, field, value):
    item = {"name": "Test", "type": "artemis", "management_url": "http://localhost:8161/console/jolokia"}
    target = item.setdefault("tls", {"enabled": True}) if field.startswith("tls.") else item
    key = field.rsplit(".", 1)[-1]
    target[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"connections": [item]}), encoding="utf-8")
    return load_config(path).connections[0]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("source,expected", [
    ("12345678", "12345678"),
    ('"12345678"', "12345678"),
    ("'12345678'", "12345678"),
    ('"00123456"', "00123456"),
    ("0", "0"),
    ('""', ""),
])
def test_literal_password_yaml_forms(tmp_path, field, source, expected):
    lines = ["connections:", "  - name: Test", "    type: artemis",
             "    management_url: http://localhost:8161/console/jolokia"]
    if field.startswith("tls."):
        lines += ["    tls:", "      enabled: true", f"      {field.split('.')[1]}: {source}"]
    else:
        lines += [f"    {field}: {source}"]
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config = load_config(path).connections[0]
    if field.startswith("tls."):
        assert config.tls[field.split(".")[1]] == expected
    else:
        assert config.get_password(management=field == "management_password") == expected


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [True, 12.5, ["secret"], None])
def test_invalid_literal_password_types_are_rejected(tmp_path, field, value):
    with pytest.raises(ConfigError, match="must be a string"):
        read_config(tmp_path, field, value)


@pytest.mark.parametrize("field", FIELDS)
def test_removed_environment_fields_explain_yaml_replacement_without_exposing_value(tmp_path, field):
    key = field.rsplit(".", 1)[-1]
    with pytest.raises(ConfigError, match=f"Use {key} with the password directly in YAML") as error:
        read_config(tmp_path, field + "_env", "literal-secret-value")
    assert "literal-secret-value" not in str(error.value)


@pytest.mark.parametrize("field", FIELDS)
def test_password_is_literal_even_when_it_matches_an_environment_variable(tmp_path, monkeypatch, field):
    monkeypatch.setenv("PASSWORD_VARIABLE", "environment-secret")
    config = read_config(tmp_path, field, "PASSWORD_VARIABLE")
    if field.startswith("tls."):
        assert config.tls[field.split(".")[1]] == "PASSWORD_VARIABLE"
    else:
        assert config.get_password(management=field == "management_password") == "PASSWORD_VARIABLE"


def test_worker_serialization_preserves_yaml_passwords_and_excludes_them_from_repr_and_drafts():
    config = ConnectionConfig("Test", "artemis", password="literal-broker-secret",
                              management_password="", tls={"trust_store_password": "literal-store-secret"})
    restored = ConnectionConfig(**config.to_dict())
    assert restored.get_password() == "literal-broker-secret"
    assert restored.get_password(management=True) == ""
    assert restored.tls["trust_store_password"] == "literal-store-secret"
    assert "literal-broker-secret" not in repr(config)
    assert "literal-store-secret" not in repr(config)
    assert "literal-broker-secret" not in json.dumps(destination(config, "DEV.ORDERS"))
    assert "literal-store-secret" not in json.dumps(destination(config, "DEV.ORDERS"))


def fake_java(monkeypatch):
    captured = []
    system = SimpleNamespace(err=object(), setOut=lambda _: None)
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(
        isJVMStarted=lambda: False, getDefaultJVMPath=lambda: "jvm",
        startJVM=lambda *args, **kwargs: captured.extend(args),
        JClass=lambda _: system))
    monkeypatch.setattr(runtime, "java_home", lambda: None)
    monkeypatch.setattr(runtime, "classpath", lambda _: [])
    return captured


def test_java_receives_literal_store_passwords(monkeypatch):
    captured = fake_java(monkeypatch)
    runtime.start_jvm("artemis", {"trust_store": "trust.jks", "trust_store_password": 12345678,
                                  "key_store": "client.jks", "key_store_password": "key-secret"})
    assert "-Djavax.net.ssl.trustStore=trust.jks" in captured
    assert "-Djavax.net.ssl.trustStorePassword=12345678" in captured
    assert "-Djavax.net.ssl.keyStorePassword=key-secret" in captured


def test_empty_literal_store_password_is_passed_to_java(monkeypatch):
    captured = fake_java(monkeypatch)
    runtime.start_jvm("artemis", {"trust_store_password": ""})
    assert "-Djavax.net.ssl.trustStorePassword=" in captured


def test_jms_connection_receives_a_literal_numeric_password(monkeypatch):
    credentials = []
    connection = SimpleNamespace(createSession=lambda *_: object(), start=lambda: None)
    factory = SimpleNamespace(createConnection=lambda user, password: credentials.append((user, password)) or connection)
    monkeypatch.setitem(sys.modules, "jpype", SimpleNamespace(JClass=lambda _: lambda _: factory))
    monkeypatch.setattr("jmsbrowser.brokers.start_jvm", lambda *_: None)
    adapter = JMSAdapter(ConnectionConfig("Test", "artemis", username="app", password=12345678))
    adapter.connect()
    assert credentials == [("app", "12345678")]


@pytest.mark.parametrize("management_password,expected", [(None, "broker-secret"), ("management-secret", "management-secret"), ("", "")])
def test_management_request_uses_its_literal_password_or_broker_fallback(monkeypatch, management_password, expected):
    credentials = []
    def urlopen(request, **_):
        credentials.append(base64.b64decode(request.get_header("Authorization").split()[1]).decode())
        return io.BytesIO(b'{"status":200,"value":[]}')
    monkeypatch.setattr("jmsbrowser.brokers.urllib.request.urlopen", urlopen)
    adapter = ArtemisAdapter(ConnectionConfig("Test", "artemis", username="app", password="broker-secret",
                                               management_username="manager", management_password=management_password,
                                               management_url="http://localhost:8161/console/jolokia"))
    adapter.jolokia({"type": "search", "mbean": "*"})
    assert credentials == ["manager:" + expected]
