"""Export the implemented UI using offline example data; no broker access."""
from dataclasses import replace
import json
from pathlib import Path

from jmsbrowser.config import Config, ConnectionConfig
from tools.sample_data import SampleAdapter
from jmsbrowser.domain import QueueInfo
from jmsbrowser.screen import ViewOptions, render
from jmsbrowser.screenshot import save_png
from jmsbrowser.state import Controller

OUTPUT = Path(__file__).resolve().parents[1] / "docs/designs"


def main():
    config = Config((ConnectionConfig("Local Artemis", "artemis"),
                     ConnectionConfig("Local IBM MQ", "ibm", port=1414, queue_manager="QM1")))
    controller = Controller(config)
    try:
        for state in controller.connections:
            adapter = SampleAdapter(state.config)
            state.status, state.queues = "connected", adapter.list_queues()
            state.queues += [QueueInfo("INT.PAYMENTS.RECONCILIATION.DLQ", 24)]
            state.queue_index = next(i for i, queue in enumerate(state.queues) if queue.name == "DEV.ORDERS")
        state = controller.current
        state.messages = SampleAdapter(state.config).browse("DEV.ORDERS")
        message = state.messages[0]
        payload = {"event": "order.created", "order_id": "ORD-1042", "total": 129.9, "currency": "EUR",
                   "note": ("Deliver to reception during office hours. Call the customer before arrival; "
                            "the entrance is on the north side of the building. ") * 2}
        body = json.dumps(payload)
        state.messages[0] = replace(message, body=body, size=len(body.encode()))
        controller.notice = "Offline example data · Drag dividers to resize; double-click a divider to reset."
        view = ViewOptions(panel_widths=(22, 44))
        controller.pane = 1
        save_png(render(controller, 160, 46, view), OUTPUT / "panels-focus-implemented.png")
        controller.pane, controller.detail_tab = 2, 0
        save_png(render(controller, 160, 46, view), OUTPUT / "body-wrap-implemented.png")
        save_png(render(controller, 120, 28, view), OUTPUT / "body-minimum-implemented.png")
        view.fullscreen = True
        save_png(render(controller, 160, 46, view), OUTPUT / "body-fullscreen-implemented.png")
        xml = '<order id="1042" currency="EUR">\n  <!-- Delivery instructions -->\n  <note>' + payload["note"] + '</note>\n</order>'
        state.messages[0] = replace(message, body=xml, size=len(xml.encode()))
        save_png(render(controller, 160, 46, view), OUTPUT / "body-xml-implemented.png")
    finally:
        controller.close()
    print(f"Saved implemented UI examples to {OUTPUT}")


if __name__ == "__main__":
    main()
