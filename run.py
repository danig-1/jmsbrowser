"""Start the browser with the local Compose configuration; does not start Docker.

Usage: python run.py [browser options]
For a separate configuration: python -m jmsbrowser --config your-config.yaml
"""
from pathlib import Path
import sys

from jmsbrowser.cli import main


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    arguments = sys.argv[1:]
    custom_config = any(arg == "--config" or arg.startswith("--config=") for arg in arguments)
    if not custom_config:
        arguments = ["--config", str(root / "config.example.yaml"), *arguments]
    raise SystemExit(main(arguments))
