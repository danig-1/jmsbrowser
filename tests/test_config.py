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


def test_example_contains_yaml_passwords_and_shows_all_queues():
    config = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert config.ui.theme == "reader"
    assert [connection.type for connection in config.connections] == ["artemis", "ibm", "artemis"]
    assert all(connection.queue_pattern == "*" for connection in config.connections)
    assert [connection.get_password() for connection in config.connections] == ["artemis-local-123", "app-local-123", "12345678"]


@pytest.mark.parametrize("changes", [{"port": True}, {"port": 65536}, {"auto_connect": "false"}, {"unknown_setting": "secret"}, {"type": "rabbitmq"}, {"timeout_seconds": 0}])
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


def test_omitted_queue_pattern_shows_all_queues(tmp_path):
    connection = load_config(write(tmp_path, base())).connections[0]
    assert connection.queue_pattern == "*"


@pytest.mark.parametrize("ui", [{"browse_limit": 0}, {"max_body_bytes": -1}, {"mouse": "yes"}, {"theme": "classic"}, {"refresh_seconds": -1}])
def test_invalid_ui_options_rejected(tmp_path, ui):
    data = base()
    data["ui"] = ui
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))
