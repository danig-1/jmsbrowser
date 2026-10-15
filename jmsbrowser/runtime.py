"""Explicit installation and checksum validation of versioned JMS provider JARs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import urllib.request


def client_dir():
    return Path(os.environ.get("JMSBROWSER_CLIENT_DIR", Path.home() / ".cache/jmsbrowser/clients"))


def manifest():
    return json.loads(Path(__file__).with_name("client-lock.json").read_text())


def install_clients(destination=None):
    directory = Path(destination) if destination else client_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for artifacts in manifest().values():
        for artifact in artifacts:
            path = directory / artifact["filename"]
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]:
                continue
            with urllib.request.urlopen(artifact["url"], timeout=60) as response:
                data = response.read(64 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                raise RuntimeError(f"Checksum mismatch for {artifact['filename']}")
            temporary = path.with_suffix(".download")
            temporary.write_bytes(data)
            temporary.replace(path)
    return directory


def classpath(provider):
    paths = []
    for artifact in manifest()[provider]:
        path = client_dir() / artifact["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
            raise RuntimeError("JMS clients missing or damaged. Run: python -m jmsbrowser --install-clients")
        paths.append(str(path))
    return paths


def java_home():
    if os.environ.get("JAVA_HOME"):
        return Path(os.environ["JAVA_HOME"])
    try:
        import jdk4py
        return Path(jdk4py.JAVA_HOME)
    except ImportError:
        return None


def start_jvm(provider, tls):
    import jpype
    if jpype.isJVMStarted():
        return
    home = java_home()
    if home:
        os.environ["JAVA_HOME"] = str(home)
    arguments = ["-Xmx256m", "-Dorg.slf4j.simpleLogger.defaultLogLevel=error"]
    for key, prop in (("trust_store", "trustStore"), ("key_store", "keyStore")):
        if tls.get(key):
            arguments.append(f"-Djavax.net.ssl.{prop}={tls[key]}")
        env_key = tls.get(key + "_password_env")
        if env_key:
            if env_key not in os.environ:
                raise ValueError(f"Environment variable {env_key} is not set.")
            arguments.append(f"-Djavax.net.ssl.{prop}Password={os.environ[env_key]}")
    jpype.startJVM(jpype.getDefaultJVMPath(), *arguments, classpath=classpath(provider), convertStrings=True)
    # Keep Java diagnostics out of the JSON worker protocol and the curses UI.
    system = jpype.JClass("java.lang.System")
    system.setOut(system.err)
