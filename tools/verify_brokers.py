"""Check local sample queues and repeat non-destructive browsing on both brokers.

Uses Docker Compose and Python's standard library. Run after `docker compose up -d`.
This validates the test environment's browse facilities, not the future browser UI.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"DEV.EMPTY": 0, "DEV.NOTIFICATIONS": 1, "DEV.ORDERS": 3, "DEV.PAYMENTS": 2}
ARTEMIS_CLIENT = (
    '/opt/artemis/bin/artemis {command} --url tcp://localhost:61616 '
    '--user "$ARTEMIS_USER" --password "$ARTEMIS_PASSWORD" {options}'
)


def compose(*args, input=None):
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, input=input, text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"Compose command failed: {' '.join(args[:4])}\n{result.stderr}\n{result.stdout}")
    return result.stdout


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for_seed():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        raw = compose("ps", "--all", "--format", "json").strip()
        services = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
        by_name = {service["Service"]: service for service in services}
        seeds = [by_name.get(name, {}) for name in ("artemis-seed", "ibm-seed", "artemis-loadtest-seed")]
        for seed in seeds:
            if seed.get("State") == "exited" and seed.get("ExitCode", 0) != 0:
                raise RuntimeError(f"{seed['Service']} failed; inspect `docker compose logs {seed['Service']}`.")
        if all(seed.get("State") == "exited" and seed.get("ExitCode") == 0 for seed in seeds):
            return
        time.sleep(1)
    raise RuntimeError("Samples were not seeded within 120 seconds; inspect `docker compose ps -a`.")


def depths(broker, names=None):
    names = EXPECTED if names is None else names
    if broker == "artemis":
        output = compose("exec", "-T", "artemis", "sh", "-c", ARTEMIS_CLIENT.format(command="queue stat", options=""))
        counts = {}
        for line in output.splitlines():
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) > 4 and cells[1] in names:
                counts[cells[1]] = int(cells[4])
        return counts
    output = compose("exec", "-T", "ibm", "runmqsc", "QM1", input="DISPLAY QLOCAL('DEV.*') CURDEPTH\n")
    return {name: int(depth) for name, depth in re.findall(r"QUEUE\((DEV\.[^)]+)\).*?CURDEPTH\((\d+)\)", output, re.S) if name in EXPECTED}


def browse(broker, queue):
    if broker == "artemis":
        command = ARTEMIS_CLIENT.format(command="browser", options=f"--destination queue://{queue} --message-count 100 --verbose")
        output = compose("exec", "-T", "artemis", "sh", "-c", command)
        ids = re.findall(r"ActiveMQMessage\[ID:([^\]]+)\]", output)
        payloads = [json.loads(body) for body in re.findall(r" browsing (\{[^\n]+\})", output)]
    else:
        output = compose("exec", "-T", "ibm", "/opt/mqm/samp/bin/amqsbcg", queue, "QM1")
        require("MQOPEN ended" not in output and "MQCONN ended" not in output, f"IBM browse failed for {queue}")
        ids = re.findall(r"MsgId : X'([0-9A-F]+)'", output)
        raw_messages, current = [], bytearray()
        for offset, hex_bytes in re.findall(r"^([0-9A-F]{8}):\s+([0-9A-F ]+) '", output, re.M):
            if offset == "00000000" and current:
                raw_messages.append(bytes(current))
                current.clear()
            current.extend(bytes.fromhex(hex_bytes))
        if current:
            raw_messages.append(bytes(current))
        payloads = [json.loads(raw.decode("utf-8")) for raw in raw_messages]
    return ids, payloads


def normalized(payloads):
    return sorted(json.dumps(payload, sort_keys=True) for payload in payloads)


def main():
    wait_for_seed()
    for broker in ("artemis", "ibm"):
        before = depths(broker)
        require(before == EXPECTED, f"{broker}: expected fresh sample depths {EXPECTED}, got {before}")
        for queue, count in EXPECTED.items():
            folder = ROOT / "dev" / "messages" / queue
            fixtures = [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]
            first_ids, first_payloads = browse(broker, queue)
            second_ids, second_payloads = browse(broker, queue)
            require(len(first_ids) == count, f"{broker}/{queue}: browsed {len(first_ids)} instead of {count}")
            require(first_ids == second_ids, f"{broker}/{queue}: message IDs changed after browsing")
            require(normalized(first_payloads) == normalized(fixtures), f"{broker}/{queue}: payloads differ from fixtures")
            require(first_payloads == second_payloads, f"{broker}/{queue}: payloads changed after browsing")
        after = depths(broker)
        require(before == after, f"{broker}: browsing changed depths from {before} to {after}")
        print(f"PASS {broker}: all 4 sample queues; identical IDs and payloads across 2 browses; depths unchanged.")
    require(depths("artemis", {"DEV.LOADTEST"}) == {"DEV.LOADTEST": 1000}, "Artemis DEV.LOADTEST must contain 1000 messages.")
    print("PASS artemis DEV.LOADTEST: 1000 messages present.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
