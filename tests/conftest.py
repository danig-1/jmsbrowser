import subprocess

import pytest


@pytest.fixture
def sample_worker(monkeypatch):
    """Substitute the test subprocess entry point without an application flag."""
    popen = subprocess.Popen

    def start(command, **options):
        if command[-2:] == ["-m", "jmsbrowser.worker"]:
            command = [*command[:-1], "tests.sample_worker"]
        return popen(command, **options)

    monkeypatch.setattr("jmsbrowser.client.subprocess.Popen", start)
