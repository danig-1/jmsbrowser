"""Start the browser with the local Compose configuration; does not start Docker.

Usage: python run.py [browser options]
For a separate configuration: python -m jmsbrowser --config your-config.yaml
"""
from pathlib import Path
import os
import sys

from jmsbrowser.cli import main


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    arguments = sys.argv[1:]
    custom_config = any(arg == "--config" or arg.startswith("--config=") for arg in arguments)
    if not custom_config:
        arguments = ["--config", str(root / "config.example.yaml"), *arguments]
        offline = any(arg in ("--demo", "--help", "-h", "--version", "--check-config", "--doctor", "--install-clients") for arg in arguments)
        if not offline:
            os.environ.setdefault("ARTEMIS_PASSWORD", "artemis-local-123")
            if "MQ_APP_PASSWORD" not in os.environ:
                os.environ["MQ_APP_PASSWORD"] = (root / "dev/secrets/mqAppPassword.txt").read_text(encoding="utf-8").strip()
    raise SystemExit(main(arguments))
