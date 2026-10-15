"""Start the local broker fixtures with Podman Compose, separately from the UI.

Usage:
    python start_podman.py                 # compose up -d
    python start_podman.py ps -a           # inspect all services
    python start_podman.py logs ibm-seed   # inspect a service
    python start_podman.py down            # stop; preserve named volumes

Requires Podman with a configured Compose provider. Uses only Python's standard
library and the same compose.yaml as Docker, from any working directory.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv) or ["up", "-d"]
    command = ["podman", "compose", "--file", str(ROOT / "compose.yaml"), *arguments]
    try:
        return subprocess.call(command, cwd=ROOT)
    except FileNotFoundError:
        print("Podman was not found. Install Podman, configure a Compose provider, and add podman to PATH.", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
