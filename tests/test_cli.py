import json

import pytest
import yaml

from jmsbrowser.cli import main, parser


def test_demo_option_is_rejected_and_absent_from_help(capsys):
    assert "--demo" not in parser().format_help()
    with pytest.raises(SystemExit) as error:
        main(["--demo"])
    assert error.value.code == 2
    assert "unrecognized arguments: --demo" in capsys.readouterr().err


def test_missing_configuration_is_reported_without_falling_back_to_sample_data(tmp_path, capsys):
    missing = tmp_path / "missing.yaml"
    assert main(["--config", str(missing), "--dump"]) == 1
    assert "Cannot read configuration" in capsys.readouterr().err


@pytest.mark.parametrize("provider", ["artemis", "ibm"])
@pytest.mark.parametrize("output", ["--dump", "--screenshot"])
def test_exports_use_yaml_connections_through_the_worker(tmp_path, capsys, sample_worker, provider, output):
    config = tmp_path / "connections.yaml"
    name = "Configured " + provider
    connection = {"name": name, "type": provider, "queue_manager": "QM1", "channel": "DEV.APP.SVRCONN"}
    if provider == "artemis":
        connection["management_url"] = "http://localhost:8161/console/jolokia"
    config.write_text(yaml.safe_dump({"connections": [connection]}))
    image = tmp_path / "screen.png"
    arguments = ["--config", str(config), "--connection", name, "--queue", "DEV.ORDERS", output]
    if output == "--screenshot":
        arguments.append(str(image))
    assert main(arguments) == 0
    captured = capsys.readouterr()
    assert not captured.err
    if output == "--dump":
        data = json.loads(captured.out)
        assert data["connection"] == name and data["queue"]["name"] == "DEV.ORDERS"
        assert len(data["messages"]) == 3
        assert all(message["message_id"].startswith("ID:sample-" + provider) for message in data["messages"])
    else:
        assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
