"""UI-independent navigation state and asynchronous broker requests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import queue
import threading

from .client import WorkerClient
from .domain import ConsumerSnapshot, Message, QueueInfo
from .outgoing import OutgoingMessage


@dataclass
class SendJob:
    connection_index: int
    epoch: int
    queue: str
    message: dict
    status: str = "sending"
    attempted: bool = False
    receipt: dict | None = None
    error: str = ""
    outcome: str = ""


@dataclass
class ConnectionState:
    config: object
    status: str = "disconnected"
    error: str = ""
    queues: list[QueueInfo] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    messages_loaded: bool = False
    queue_index: int = 0
    message_index: int = 0
    generation: int = 0
    epoch: int = 0
    busy: bool = False
    depth_jobs: int = 0
    depth_pending: set[str] = field(default_factory=set)
    all_depths_pending: bool = False
    consumer_snapshot: ConsumerSnapshot | None = None
    consumer_status: str = "idle"
    consumer_error: str = ""
    consumer_generation: int = 0
    consumer_index: int = 0
    client: object = None
    executor: object = None

    @property
    def selected_queue(self):
        return self.queues[self.queue_index] if 0 <= self.queue_index < len(self.queues) else None

    @property
    def selected_message(self):
        return self.messages[self.message_index] if 0 <= self.message_index < len(self.messages) else None

    def clear_consumers(self):
        self.consumer_generation += 1
        self.consumer_snapshot, self.consumer_status, self.consumer_error, self.consumer_index = None, "idle", "", 0


class Controller:
    def __init__(self, config, demo=False):
        self.config, self.demo = config, demo
        self.connections = [ConnectionState(connection) for connection in config.connections]
        self.connection_index, self.pane, self.detail_tab, self.detail_scroll = 0, 0, 1, 0
        self.results = queue.Queue()
        self.send_results, self.send_job = queue.Queue(), None
        self.notice = "Enter connects a server. Tab or click changes panels."
        self.closed = False
        for index, connection in enumerate(self.connections):
            if connection.config.auto_connect or demo:
                self.connect(index)

    @property
    def current(self):
        return self.connections[self.connection_index]

    def connect(self, index=None):
        index = self.connection_index if index is None else index
        state = self.connections[index]
        if state.status in ("connecting", "connected"):
            return
        state.epoch += 1
        state.status, state.error, state.busy = "connecting", "", True
        state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="broker")
        state.client = WorkerClient(state.config, self.demo)
        self._submit(index, "connect")

    def disconnect(self, index=None):
        index = self.connection_index if index is None else index
        state = self.connections[index]
        if self.send_job and self.send_job.connection_index == index and self.send_job.status == "sending":
            self.send_job.status, self.send_job.error = "error", "Connection closed before a send result was received."
            self.send_job.outcome = "unknown" if self.send_job.attempted else "not_sent"
        state.epoch += 1
        state.generation += 1
        if state.client:
            old_client = state.client
            old_client.abort()
            threading.Thread(target=old_client.close, daemon=True).start()
            state.client = None
        if state.executor:
            state.executor.shutdown(wait=False, cancel_futures=True)
        state.status, state.busy, state.depth_jobs = "disconnected", False, 0
        state.depth_pending.clear()
        state.all_depths_pending = False
        state.queues, state.messages = [], []
        state.messages_loaded = False
        state.clear_consumers()

    def select_connection(self, index):
        self.connection_index = max(0, min(index, len(self.connections) - 1))
        self.detail_scroll = 0

    def select_queue(self, index):
        state = self.current
        if not state.queues:
            return
        index = max(0, min(index, len(state.queues) - 1))
        if index == state.queue_index:
            return
        state.clear_consumers()
        state.queue_index = index
        state.messages, state.message_index, state.messages_loaded = [], 0, False
        state.generation += 1
        state.busy = False
        self.detail_scroll = 0
        self.notice = f"{state.selected_queue.name}: press Enter to load messages."

    def browse_queue(self, index=None):
        index = self.connection_index if index is None else index
        state = self.connections[index]
        if state.status != "connected" or not state.selected_queue or state.busy:
            return
        state.generation += 1
        state.busy = True
        self._submit(index, "browse")

    def select_message(self, index):
        self.current.message_index = max(0, min(index, len(self.current.messages) - 1))
        self.detail_scroll = 0

    def refresh(self):
        state = self.current
        if state.status != "connected":
            self.connect()
            return
        state.generation += 1
        state.busy = True
        self._submit(self.connection_index, "queues")

    def refresh_queue_depths(self):
        state = self.current
        if state.status != "connected":
            self.notice = "Connect a server to refresh queue depths."
            return
        if not state.all_depths_pending:
            state.depth_jobs += 1
            state.all_depths_pending = True
            self._submit(self.connection_index, "queue_depths")

    def refresh_queue_depth(self):
        state = self.current
        if state.status == "connected" and state.selected_queue and state.selected_queue.name not in state.depth_pending:
            state.depth_jobs += 1
            state.depth_pending.add(state.selected_queue.name)
            self._submit(self.connection_index, "queue_depth")

    def refresh_consumers(self):
        state = self.current
        if state.status != "connected" or not state.selected_queue:
            self.notice = "Select a connected server and queue to inspect consumers."
            return False
        if state.consumer_status != "loading":
            state.consumer_status, state.consumer_error = "loading", ""
            state.consumer_generation += 1
            self._submit(self.connection_index, "consumers")
        return True

    def send_message(self, index, epoch, queue_name, draft):
        if self.send_job and self.send_job.status == "sending":
            raise ValueError("A send is already in progress.")
        state = self.connections[index]
        if state.epoch != epoch or state.status != "connected":
            raise ValueError("The composer connection changed. Reconnect and open a new composer.")
        if queue_name not in {item.name for item in state.queues}:
            raise ValueError("The destination is no longer in the configured queue list.")
        snapshot = OutgoingMessage.from_dict(draft.to_dict()).to_dict()
        job = self.send_job = SendJob(index, epoch, queue_name, snapshot)
        client = state.client
        def run():
            try:
                if state.epoch != epoch or state.status != "connected":
                    from .client import BrokerError
                    raise BrokerError("Connection changed before sending.", "not_sent")
                job.attempted = True
                result = client.request("send", queue=queue_name, message=snapshot)
                self.send_results.put((job, result, None))
            except Exception as error:
                self.send_results.put((job, None, error))
        state.executor.submit(run)
        return job

    def poll_sends(self):
        while True:
            try:
                job, receipt, error = self.send_results.get_nowait()
            except queue.Empty:
                return
            if job is not self.send_job:
                continue
            state = self.connections[job.connection_index]
            if error:
                job.status, job.error = "error", str(error)
                job.outcome = getattr(error, "outcome", None) or "unknown"
                if state.client and getattr(state.client, "closed", False) and state.epoch == job.epoch:
                    self.disconnect(job.connection_index)
                    state.status, state.error = "error", str(error)
                self.notice = "Send not confirmed: " + job.error
            else:
                job.status, job.receipt, job.outcome = "sent", receipt, "sent"
                self.notice = f"Sent one message to {job.queue}: {receipt.get('JMSMessageID') or 'ID unavailable'}."
                if state.epoch == job.epoch and state.status == "connected" and state.selected_queue and state.selected_queue.name == job.queue:
                    if job.connection_index == self.connection_index:
                        self.refresh_queue_depth()
                    state.generation += 1
                    state.busy = True
                    self._submit(job.connection_index, "browse")

    def _submit(self, index, operation):
        state = self.connections[index]
        epoch, client = state.epoch, state.client
        generation = state.consumer_generation if operation == "consumers" else state.generation
        selected = state.selected_queue
        queue_name = selected.name if selected else ""
        depth_only = operation in ("queue_depths", "queue_depth")

        def run():
            current_generation = state.consumer_generation if operation == "consumers" else state.generation
            if state.epoch != epoch or (operation != "connect" and not depth_only and current_generation != generation):
                return
            try:
                arguments = dict(queue=queue_name, limit=self.config.ui.browse_limit, max_body_bytes=self.config.ui.max_body_bytes) if operation == "browse" else {}
                if operation in ("queue_depth", "consumers"):
                    arguments = dict(queue=queue_name)
                value = client.request("queues" if operation == "queue_depths" else operation, **arguments)
                self.results.put((index, epoch, generation, operation, value, None))
            except Exception as error:
                self.results.put((index, epoch, generation, operation, None, str(error)))
        state.executor.submit(run)

    def poll(self):
        for index, state in enumerate(self.connections):
            if state.client and state.status in ("connected", "connecting"):
                try:
                    event = state.client.events.get_nowait()
                except queue.Empty:
                    pass
                else:
                    self.disconnect(index)
                    state.status, state.error = "error", event["error"]
                    state.consumer_status, state.consumer_error = "error", event["error"]
        while True:
            try:
                index, epoch, generation, operation, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            state = self.connections[index]
            depth_only = operation in ("queue_depths", "queue_depth")
            current_generation = state.consumer_generation if operation == "consumers" else state.generation
            if state.epoch != epoch or (operation != "connect" and not depth_only and current_generation != generation):
                continue
            if operation == "consumers":
                if error:
                    if getattr(state.client, "closed", False):
                        self.disconnect(index)
                        state.status, state.error = "error", error
                    state.consumer_status, state.consumer_error = "error", error
                elif state.selected_queue and value["queue"] == state.selected_queue.name:
                    old = state.consumer_snapshot
                    selected = old.consumers[state.consumer_index] if old and state.consumer_index < len(old.consumers) else None
                    state.consumer_snapshot = ConsumerSnapshot.from_dict(value)
                    state.consumer_index = next((i for i, item in enumerate(state.consumer_snapshot.consumers) if selected and item.identity == selected.identity), 0)
                    state.consumer_status = "ready"
                continue
            if depth_only:
                state.depth_jobs = max(0, state.depth_jobs - 1)
            else:
                state.busy = False
            if error:
                self.disconnect(index)
                state.status, state.error = "error", error
                self.notice = error
                continue
            if operation == "queue_depth":
                state.depth_pending.discard(value["name"])
                state.queues = [replace(q, depth=int(value["depth"])) if q.name == value["name"] else q for q in state.queues]
                self.notice = f"{value['name']}: depth refreshed ({value['depth']})."
            elif operation == "queue_depths":
                state.all_depths_pending = False
                previous = state.selected_queue.name if state.selected_queue else None
                state.queues = [QueueInfo(**item) for item in value]
                state.queue_index = next((i for i, q in enumerate(state.queues) if q.name == previous), 0)
                if not state.selected_queue or state.selected_queue.name != previous:
                    state.clear_consumers()
                    state.generation += 1
                    state.messages, state.message_index, state.busy = [], 0, False
                    state.messages_loaded = False
                self.notice = f"{state.config.name}: refreshed all {len(state.queues)} queue depths."
            elif operation in ("connect", "queues"):
                previous = state.selected_queue.name if state.selected_queue else None
                requested = None
                state.status, state.error = "connected", ""
                state.queues = [QueueInfo(**item) for item in value]
                state.queue_index = next((i for i, q in enumerate(state.queues) if q.name == previous), 0)
                if operation == "connect" and previous is None:
                    state.queue_index = next((i for i, q in enumerate(state.queues) if q.depth), 0)
                if operation == "connect" and index == self.connection_index and getattr(self, "initial_queue", None):
                    requested = self.initial_queue
                    state.queue_index = next((i for i, q in enumerate(state.queues) if q.name == requested), state.queue_index)
                    self.initial_queue = None
                if not state.selected_queue or state.selected_queue.name != previous:
                    state.clear_consumers()
                    state.messages, state.message_index, state.messages_loaded = [], 0, False
                state.generation += 1
                if state.selected_queue and (requested or state.messages_loaded):
                    self.browse_queue(index)
                self.notice = f"{state.config.name}: {len(state.queues)} queues."
                if state.selected_queue and not state.messages_loaded and not state.busy:
                    self.notice += " Press Enter on a queue to load messages."
            else:
                previous = state.selected_message.message_id if state.selected_message else None
                state.messages = [Message.from_dict(item) for item in value]
                state.messages_loaded = True
                state.message_index = next((i for i, m in enumerate(state.messages) if m.message_id == previous), 0)
                self.notice = f"{state.config.name}: loaded {len(state.messages)} messages; queue unchanged."
        # Clear earlier read results before scheduling post-send refreshes.
        self.poll_sends()

    def close(self):
        self.closed = True
        for index, state in enumerate(self.connections):
            state.epoch += 1
            if state.client:
                state.client.close()
            if state.executor:
                state.executor.shutdown(wait=False, cancel_futures=True)
