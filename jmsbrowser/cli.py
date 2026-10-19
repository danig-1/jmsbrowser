from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import sys
import time

from . import __version__
from .config import load_config
from .domain import Message, QueueInfo


def parser():
    result = argparse.ArgumentParser(description="Non-consuming curses queue browser and message composer for IBM MQ and Apache Artemis.")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", default="config.yaml", help="YAML connection file (default: config.yaml)")
    result.add_argument("--check-config", action="store_true", help="Validate YAML and exit")
    result.add_argument("--install-clients", action="store_true", help="Download checksum-pinned JMS provider JARs and exit")
    result.add_argument("--doctor", action="store_true", help="Check Java and installed provider clients")
    result.add_argument("--connection", help="Initially selected connection name")
    result.add_argument("--queue", help="Initially selected queue name")
    result.add_argument("--dump", action="store_true", help="Browse once and print JSON; no terminal UI")
    result.add_argument("--screenshot", metavar="PNG", help="Export the application screen and exit; requires Pillow")
    result.add_argument("--width", type=int, default=160, help="Screenshot width in columns")
    result.add_argument("--height", type=int, default=46, help="Screenshot height in rows")
    result.add_argument("--font", help="Monospaced screenshot font path")
    return result


def read_once(config, index, queue_name):
    from .client import WorkerClient
    connection = config.connections[index]
    with WorkerClient(connection) as client:
        queues = [QueueInfo(**q) for q in client.request("connect")]
        selected = next((q for q in queues if q.name == queue_name), None) if queue_name else next((q for q in queues if q.depth), queues[0] if queues else None)
        if queue_name and selected is None:
            raise ValueError(f"Queue {queue_name} is not in the configured queue list.")
        messages = client.request("browse", queue=selected.name, limit=config.ui.browse_limit, max_body_bytes=config.ui.max_body_bytes) if selected else []
        return queues, selected, messages


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.install_clients:
            from .runtime import install_clients
            print(f"JMS clients installed: {install_clients()}")
            return 0
        if args.doctor:
            from .runtime import classpath, java_home
            import jpype
            home = java_home()
            if home:
                import os
                os.environ["JAVA_HOME"] = str(home)
            print(f"JVM: {jpype.getDefaultJVMPath()}")
            for provider in ("artemis", "ibm"):
                print(f"{provider}: {len(classpath(provider))} verified client JARs")
            return 0
        config = load_config(args.config)
        if args.check_config:
            print(f"Configuration valid: {len(config.connections)} connections; reader layout.")
            return 0
        index = next((i for i, c in enumerate(config.connections) if c.name == args.connection), 0)
        if args.connection and config.connections[index].name != args.connection:
            raise ValueError(f"Connection {args.connection} is not configured.")
        if args.dump or args.screenshot:
            queues, selected, messages = read_once(config, index, args.queue)
            if args.dump:
                print(json.dumps({"connection": config.connections[index].name, "queues": [asdict(q) for q in queues], "queue": asdict(selected) if selected else None, "messages": messages}, indent=2, ensure_ascii=True))
            if args.screenshot:
                from .state import Controller
                from .screen import render
                from .screenshot import save_png
                # Construct the view from the real adapter's result; avoid new connections.
                controller = Controller(replace(config, connections=tuple(replace(c, auto_connect=False) for c in config.connections)))
                controller.connection_index, controller.pane = index, 2
                state = controller.current
                state.status, state.queues = "connected", queues
                state.queue_index = next((i for i, q in enumerate(queues) if selected and q.name == selected.name), 0)
                state.messages = [Message.from_dict(m) for m in messages]
                state.messages_loaded = True
                controller.notice = "Messages remain on the queue. Size = payload bytes."
                if not 120 <= args.width <= 400 or not 28 <= args.height <= 200:
                    raise ValueError("Screenshot dimensions must be 120–400 columns and 28–200 rows.")
                print(save_png(render(controller, args.width, args.height), args.screenshot, args.font))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("Interactive mode requires a terminal. Use --dump or --screenshot for non-interactive output.")
        from .state import Controller
        from .ui import run
        controller = Controller(config)
        controller.connection_index = index
        if args.queue:
            # Initial queue selection is applied once discovery completes.
            controller.initial_queue = args.queue
            controller.connect(index)
        run(controller)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"jmsbrowser: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
