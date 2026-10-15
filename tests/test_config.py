from pathlib import Path
import pytest
import yaml

from jmsbrowser.config import ConfigError, load_config


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def base():
    return {"connections": [{"name": "Test", "type": "artemis", "management_url": "http://localhost:8161/console/jolokia"}]}


def test_example_loads_without_credentials():
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert config.ui.theme == "reader"
    assert [connection.type for connection in config.connections] == ["artemis", "ibm"]


@pytest.mark.parametrize("changes", [{"port": True}, {"port": 65536}, {"auto_connect": "false"}, {"password": "secret"}, {"type": "rabbitmq"}, {"timeout_seconds": 0}])
def test_invalid_connection_options_rejected(tmp_path, changes):
    data = base()
    data["connections"][0].update(changes)
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


def test_duplicate_names_rejected(tmp_path):
    data = base()
    data["connections"] *= 2
    with pytest.raises(ConfigError, match="Duplicate"):
        load_config(write(tmp_path, data))


def test_missing_environment_variable_has_clear_error(tmp_path, monkeypatch):
    data = base()
    data["connections"][0]["password_env"] = "JMSBROWSER_MISSING_TEST_PASSWORD"
    monkeypatch.delenv("JMSBROWSER_MISSING_TEST_PASSWORD", raising=False)
    connection = load_config(write(tmp_path, data)).connections[0]
    with pytest.raises(ConfigError, match="not set"):
        connection.password()


@pytest.mark.parametrize("ui", [{"browse_limit": 0}, {"max_body_bytes": -1}, {"mouse": "yes"}, {"theme": "classic"}, {"refresh_seconds": -1}])
def test_invalid_ui_options_rejected(tmp_path, ui):
    data = base()
    data["ui"] = ui
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))
