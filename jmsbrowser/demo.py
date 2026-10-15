"""Deterministic offline data for trying navigation without Java or brokers."""
from __future__ import annotations

import json
import time
import uuid
from .domain import Consumer, ConsumerSnapshot, Message, QueueInfo


class DemoAdapter:
    def __init__(self, config):
        self.config = config
        orders = [
            {"event": "order.created", "order_id": "ORD-1042", "customer": "Ada Lovelace", "total": 129.90, "currency": "EUR", "items": [{"sku": "KB-01", "quantity": 1}], "source": "web-shop"},
            {"event": "order.created", "order_id": "ORD-1043", "customer": "Grace Hopper", "total": 74.50, "currency": "EUR", "source": "mobile-app"},
            {"event": "order.updated", "order_id": "ORD-1044", "customer": "Alan Turing", "total": 219, "currency": "EUR", "status": "paid"},
        ]
        self.payloads = {"DEV.ORDERS": orders, "DEV.PAYMENTS": [{"event": "payment.received", "amount": 129.90}, {"event": "payment.pending", "amount": 74.50}], "DEV.NOTIFICATIONS": [{"event": "notification.email", "recipient": "ada@example.test"}], "DEV.EMPTY": []}
        self.sent = {name: [] for name in self.payloads}

    def connect(self):
        pass

    def close(self):
        pass

    def list_queues(self):
        return [QueueInfo(name, len(values) + len(self.live_sent(name)), "ANYCAST" if self.config.type == "artemis" else "LOCAL") for name, values in sorted(self.payloads.items())]

    def queue_depth(self, queue_name):
        return len(self.payloads[queue_name]) + len(self.live_sent(queue_name))

    def live_sent(self, queue_name):
        now = int(time.time() * 1000)
        return [message for message in self.sent[queue_name] if not message.headers["JMSExpiration"] or message.headers["JMSExpiration"] > now]

    def send_message(self, queue_name, draft):
        values = draft.validate()
        mid, now = "ID:demo-" + str(uuid.uuid4()), int(time.time() * 1000)
        headers = {"JMSMessageID": mid, "JMSTimestamp": now, "JMSExpiration": now + draft.ttl_ms if draft.ttl_ms else 0,
                   "JMSType": draft.jms_type or None, "JMSCorrelationID": draft.correlation_id or None,
                   "JMSPriority": draft.priority, "JMSDeliveryMode": 2 if draft.persistent else 1,
                   "JMSRedelivered": False, "JMSDestination": queue_name}
        size = len(draft.body.encode())
        self.sent[queue_name].append(Message(mid, "TextMessage", draft.priority, draft.body, size, headers,
                                            {name: value for name, _, value in values}))
        return {key: headers[key] for key in ("JMSMessageID", "JMSTimestamp", "JMSExpiration")} | {"queue": queue_name, "size": size, "demo": True}

    def consumers(self, queue_name):
        consumers = [] if queue_name != "DEV.ORDERS" else [
            Consumer("c-184", "orders-worker-1", "10.20.0.21:50412", "orders-app", "CORE", "", "conn-18", "session-4", "OK"),
            Consumer("c-185", "orders-worker-2", "10.20.0.22:50418", "orders-app", "CORE", "", "conn-19", "session-5", "OK"),
            Consumer("c-203", "audit-service", "10.20.0.30:51102", "audit-app", "AMQP", "", "conn-20", "session-6", "OK"),
        ]
        return ConsumerSnapshot(queue_name, consumers, note="Illustrative demo consumers; no broker connection.")

    def browse(self, queue_name, limit=100, max_body_bytes=262144):
        messages = []
        for index, payload in enumerate(self.payloads[queue_name][:limit]):
            body = json.dumps(payload, ensure_ascii=False)
            size = len(body.encode("utf-8"))
            mid = f"ID:demo-{self.config.type}-{queue_name}-{index + 1:04}"
            messages.append(Message(mid, "TextMessage", 4, body.encode()[:max_body_bytes].decode(errors="ignore"), size,
                                    {"JMSMessageID": mid, "JMSPriority": 4, "JMSDeliveryMode": 2, "JMSExpiration": 0},
                                    {"source": "demo"}, size > max_body_bytes))
        for message in self.live_sent(queue_name)[:max(0, limit - len(messages))]:
            from dataclasses import replace
            preview = message.body.encode()[:max_body_bytes].decode(errors="ignore")
            messages.append(replace(message, body=preview, truncated=message.size > max_body_bytes))
        return messages
