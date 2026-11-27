"""Send a queued ball to a bowling machine.

VANSH_MACHINE picks how:

- unset: the simulator. Each ball goes straight to the bowled log.
- host:port: a machine on the same network as VANSH, one JSON line each way.
- edge: the machine is at the nets and VANSH is on a server. "Bowl" puts the
  ball in a request list, and the edge bridge at the nets (crickcoach.edge)
  collects it, counts down, and bowls it. The bowling machine never listens
  to the internet.

The line protocol:

    {"cmd": "bowl", "id": "ab12cd34", "pace_kph": 125, "line": "off stump",
     "length": "good", "length_m": 7.0, "offset_m": -0.1, "batter": "right"}

and the machine replies:

    {"ok": true, "id": "ab12cd34"}

length_m is metres from the batter's stumps. offset_m is metres across from
middle stump, positive on the leg side of a right-handed batter; for a
left-handed batter it is already mirrored, so the machine aims where it says.

A radar gun can push one number per line to the port in VANSH_RADAR_PORT.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timezone


class MachineLink:
    def __init__(self, endpoint: str | None = None):
        endpoint = (endpoint or "").strip()
        self.endpoint = endpoint or None

    @property
    def mode(self) -> str:
        if not self.endpoint:
            return "simulator"
        if self.endpoint.lower() == "edge":
            return "edge"
        return self.endpoint

    @property
    def is_edge(self) -> bool:
        return self.mode == "edge"

    def bowl(self, item: dict) -> dict:
        command = command_for(item)
        if self.endpoint and not self.is_edge:
            send(self.endpoint, command)
            via = self.endpoint
        else:
            via = "simulator"
        return {**command, "via": via}


def command_for(item: dict) -> dict:
    offset = item.get("offset_m")
    if offset is not None and item.get("batter") == "left":
        offset = -offset
    return {
        "cmd": "bowl",
        "id": item["id"],
        "pace_kph": item.get("pace_kph"),
        "line": item["line"],
        "length": item["length"],
        "length_m": item.get("length_m"),
        "offset_m": offset,
        "batter": item.get("batter") or "right",
        "note": item.get("note") or "",
    }


def send(endpoint: str, command: dict, *, timeout: float = 3.0) -> dict:
    host, _, port_text = endpoint.rpartition(":")
    if not host or not port_text.isdigit():
        raise RuntimeError("The machine address must look like host:8520.")
    payload = (json.dumps(command) + "\n").encode()
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout) as sock:
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while b"\n" not in data and len(data) < 4000:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                data += chunk
    except OSError as exc:
        raise RuntimeError(f"The machine at {endpoint} did not answer. {exc}") from exc
    if not data:
        raise RuntimeError("The machine accepted the connection and sent no reply.")
    try:
        reply = json.loads(data.splitlines()[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("The machine reply was not JSON.") from exc
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "The machine refused the ball.")
    return reply


def serve(host: str, port: int, stop: threading.Event, log: list[dict]) -> None:
    """A reference machine. It records each ball and answers ok."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen()
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                data = b""
                conn.settimeout(2)
                try:
                    while b"\n" not in data:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        data += chunk
                    command = json.loads(data.decode() or "{}")
                    log.append(command)
                    reply = {"ok": True, "id": command.get("id")}
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except (OSError, json.JSONDecodeError):
                    continue
    finally:
        server.close()


def listen_radar(port: int, readings: dict, stop: threading.Event) -> None:
    """Read pace numbers, one per line, from a radar gun on localhost.

    readings is shared with the app and is updated in place.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen()
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                data = b""
                conn.settimeout(2)
                try:
                    while b"\n" not in data and len(data) < 200:
                        chunk = conn.recv(200)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    continue
                pace = read_radar_line(data.decode("utf-8", "replace"))
                if pace is not None:
                    readings.update(
                        pace_kph=pace,
                        at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    )
    finally:
        server.close()


def read_radar_line(line: str) -> float | None:
    text = line.strip().split()[-1] if line.strip() else ""
    try:
        pace = float(text)
    except ValueError:
        return None
    if 40 <= pace <= 180:
        return round(pace, 1)
    return None
