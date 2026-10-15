from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


def integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"{label} must be an integer between {minimum} and {maximum}.")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ConfigError(f"{label} must be true or false.")
    return value


def string(value, label, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ConfigError(f"{label} must be a {'non-empty ' if required else ''}string.")
    return value


@dataclass(frozen=True)
class ConnectionConfig:
    name: str
    type: str
    host: str = "localhost"
    port: int = 61616
    username: str = ""
    password_env: str = ""
    queue_manager: str = ""
    channel: str = ""
    queue_pattern: str = "*"
    queues: tuple[str, ...] = ()
    model_queue: str = "SYSTEM.DEFAULT.MODEL.QUEUE"
    reply_prefix: str = "JMSBROWSER.*"
    management_url: str = ""
    management_username: str = ""
    management_password_env: str = ""
    management_ca_file: str = ""
    auto_connect: bool = False
    timeout_seconds: int = 15
    tls: dict = field(default_factory=dict)

    def password(self, management=False):
        key = self.management_password_env if management and self.management_password_env else self.password_env
        if not key:
            return ""
        if key not in os.environ:
            raise ConfigError(f"{self.name}: environment variable {key} is not set.")
        return os.environ[key]

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class UIConfig:
    theme: str = "reader"
    browse_limit: int = 100
    max_body_bytes: int = 262144
    refresh_seconds: int = 0
    mouse: bool = True
    ascii: bool = False


@dataclass(frozen=True)
class Config:
    connections: tuple[ConnectionConfig, ...]
    ui: UIConfig = field(default_factory=UIConfig)


def load_config(path: str | Path) -> Config:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Cannot read configuration {path}: {error}") from error
    if not isinstance(data, dict) or set(data) - {"connections", "ui"}:
        raise ConfigError("Configuration must contain connections and optional ui settings.")
    raw_connections = data.get("connections")
    if not isinstance(raw_connections, list) or not raw_connections:
        raise ConfigError("connections must be a non-empty list.")
    connections, names = [], set()
    fields = ConnectionConfig.__dataclass_fields__
    for entry in raw_connections:
        if not isinstance(entry, dict) or set(entry) - fields.keys():
            raise ConfigError(f"Unknown connection settings: {set(entry) - fields.keys() if isinstance(entry, dict) else entry}")
        options = dict(entry)
        name = string(options.get("name"), "connection.name", True)
        kind = string(options.get("type"), f"{name}.type", True)
        if kind not in ("ibm", "artemis"):
            raise ConfigError(f"{name}: type must be ibm or artemis.")
        if name in names:
            raise ConfigError(f"Duplicate connection name: {name}")
        names.add(name)
        options.setdefault("port", 1414 if kind == "ibm" else 61616)
        integer(options["port"], f"{name}.port", 1, 65535)
        integer(options.get("timeout_seconds", 15), f"{name}.timeout_seconds", 1, 120)
        boolean(options.get("auto_connect", False), f"{name}.auto_connect")
        for key in fields:
            if key in options and key not in ("port", "timeout_seconds", "auto_connect", "queues", "tls"):
                string(options[key], f"{name}.{key}")
        queues = options.get("queues", [])
        if not isinstance(queues, (list, tuple)) or any(not isinstance(q, str) or not q for q in queues):
            raise ConfigError(f"{name}.queues must be a list of queue names.")
        options["queues"] = tuple(queues)
        tls = options.get("tls", {})
        tls_fields = {"enabled", "trust_store", "trust_store_password_env", "key_store", "key_store_password_env", "cipher_suite"}
        if not isinstance(tls, dict) or set(tls) - tls_fields:
            raise ConfigError(f"{name}.tls contains unknown settings.")
        boolean(tls.get("enabled", False), f"{name}.tls.enabled")
        for key, value in tls.items():
            if key != "enabled":
                string(value, f"{name}.tls.{key}", True)
        if kind == "ibm" and (not options.get("queue_manager") or not options.get("channel")):
            raise ConfigError(f"{name}: IBM requires queue_manager and channel.")
        if kind == "ibm" and tls.get("enabled") and not tls.get("cipher_suite"):
            raise ConfigError(f"{name}: IBM TLS requires cipher_suite.")
        if kind == "artemis" and not options.get("management_url"):
            raise ConfigError(f"{name}: Artemis requires management_url for queue discovery.")
        connections.append(ConnectionConfig(**options))
    raw_ui = data.get("ui", {})
    if not isinstance(raw_ui, dict) or set(raw_ui) - UIConfig.__dataclass_fields__.keys():
        raise ConfigError("ui contains unknown settings.")
    ui = UIConfig(**raw_ui)
    if ui.theme != "reader":
        raise ConfigError("ui.theme must be reader.")
    integer(ui.browse_limit, "ui.browse_limit", 1, 10000)
    integer(ui.max_body_bytes, "ui.max_body_bytes", 1024, 16777216)
    integer(ui.refresh_seconds, "ui.refresh_seconds", 0, 3600)
    boolean(ui.mouse, "ui.mouse")
    boolean(ui.ascii, "ui.ascii")
    return Config(tuple(connections), ui)
