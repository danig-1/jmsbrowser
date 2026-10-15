"""One persistent provider connection per isolated process; JSON-lines protocol."""
from __future__ import annotations

from dataclasses import asdict
import json
import sys
import threading

from .config import ConnectionConfig
from .outgoing import OutgoingMessage, SendFailure


def main():
    adapter = None
    known_queues = set()
    output_lock = threading.Lock()

    def send(value):
        with output_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=True) + "\n")
            sys.stdout.flush()

    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                operation = request["op"]
                if operation == "connect":
                    config = ConnectionConfig(**request["config"])
                    if request.get("demo"):
                        from .demo import DemoAdapter
                        adapter = DemoAdapter(config)
                    else:
                        from .brokers import create_adapter
                        adapter = create_adapter(config)
                        adapter.on_error = lambda error: send({"event": "connection_error", "error": str(error)})
                    adapter.connect()
                    result = [asdict(queue) for queue in adapter.list_queues()]
                    known_queues = {queue["name"] for queue in result}
                elif operation == "queues":
                    result = [asdict(queue) for queue in adapter.list_queues()]
                    known_queues = {queue["name"] for queue in result}
                elif operation == "queue_depth":
                    result = {"name": request["queue"], "depth": adapter.queue_depth(request["queue"])}
                elif operation == "consumers":
                    result = adapter.consumers(request["queue"]).to_dict()
                elif operation == "browse":
                    result = [message.to_dict() for message in adapter.browse(request["queue"], request["limit"], request["max_body_bytes"])]
                elif operation == "full_message":
                    result = adapter.full_message(request["queue"], request["message_id"]).to_dict()
                elif operation == "send":
                    try:
                        if request["queue"] not in known_queues:
                            raise ValueError("Sending requires a queue in the discovered, configured queue list.")
                        draft = OutgoingMessage.from_dict(request["message"])
                    except (ValueError, KeyError) as error:
                        raise SendFailure(str(error)) from error
                    result = adapter.send_message(request["queue"], draft)
                elif operation == "close":
                    send({"ok": True, "result": None})
                    break
                else:
                    raise ValueError(f"Unknown operation {operation}")
                send({"ok": True, "result": result})
            except Exception as error:
                send({"ok": False, "error": str(error) or type(error).__name__, "outcome": getattr(error, "outcome", None)})
    finally:
        if adapter is not None:
            adapter.close()


if __name__ == "__main__":
    main()
