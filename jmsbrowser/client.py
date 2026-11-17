from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading


WORKER_STOPPED = "Broker worker stopped. Reconnect to try again."


class BrokerError(RuntimeError):
    def __init__(self, message, outcome=None):
        super().__init__(message)
        self.outcome = outcome


class WorkerClient:
    def __init__(self, config):
        self.config = config
        self.responses, self.events = queue.Queue(), queue.Queue()
        self.lock = threading.Lock()
        self._close_lock = threading.Lock()
        self.closed = False
        self.log = tempfile.TemporaryFile(mode="w+b")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen([sys.executable, "-m", "jmsbrowser.worker"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, encoding="utf-8",
                                        creationflags=flags, bufsize=1, cwd=Path(__file__).resolve().parents[1])
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    (self.events if "event" in value else self.responses).put(value)
        except (OSError, ValueError):
            pass
        finally:
            self._mark_closed(WORKER_STOPPED)

    def _mark_closed(self, error=None):
        # This lock must be separate from the request lock: a request may be
        # waiting for the reader to report EOF while holding that lock.
        with self._close_lock:
            if self.closed:
                return False
            self.closed = True
            if error is not None:
                self.responses.put({"ok": False, "error": error})
                self.events.put({"event": "connection_error", "error": error})
            return True

    def request(self, operation, **kwargs):
        with self.lock:
            if self.closed:
                raise BrokerError("Connection is closed.", "not_sent" if operation == "send" else None)
            payload = {"op": operation, **kwargs}
            if operation == "connect":
                payload.update(config=self.config.to_dict())
            try:
                self.process.stdin.write(json.dumps(payload) + "\n")
                self.process.stdin.flush()
                response = self.responses.get(timeout=self.config.timeout_seconds)
            except queue.Empty:
                self.abort()
                raise BrokerError(f"{self.config.name}: {operation} timed out after {self.config.timeout_seconds}s. Reconnect to retry.", "unknown" if operation == "send" else None) from None
            except (BrokenPipeError, OSError, ValueError) as error:
                raise BrokerError(WORKER_STOPPED, "unknown" if operation == "send" else None) from error
            if not response.get("ok"):
                raise BrokerError(response.get("error", "Broker operation failed."), response.get("outcome") or ("unknown" if operation == "send" else None))
            return response.get("result")

    def abort(self):
        self._mark_closed()
        with suppress(ProcessLookupError):
            if self.process.poll() is None:
                self.process.kill()
        self.responses.put({"ok": False, "error": "Connection closed."})

    def close(self):
        if self._mark_closed() and self.lock.acquire(blocking=False):
            try:
                self.process.stdin.write('{"op":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.abort()
            finally:
                self.lock.release()
        else:
            self.abort()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.log):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
