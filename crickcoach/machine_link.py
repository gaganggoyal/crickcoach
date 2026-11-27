"""Send a queued ball to a bowling machine.

With no address set, CrickCoach keeps the ball in a local bowled log. That is
the simulator. A real machine speaks one JSON object per line on a TCP port:

    {"cmd": "bowl", "id": "ab12cd34", "pace_kph": 125, "line": "off", "length": "good"}

It replies:

    {"ok": true, "id": "ab12cd34"}

Set CRICKCOACH_MACHINE=192.168.1.50:8520 to use that port. A radar gun can
push a number, one reading per line, to the port in CRICKCOACH_RADAR_PORT.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timezone


class MachineLink:
    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint.strip() if endpoint else None

    def bowl(self, item: dict) -> dict:
        command = {
            "cmd": "bowl",
            "id": item["id"],
            "pace_kph": item.get("pace_kph"),
            "line": item["line"],
            "length": item["length"],
            "note": item.get("note") or "",
        }
        if self.endpoint:
            _send(self.endpoint, command)
            via = self.endpoint
        else:
            via = "simulator"
        return {**command, "via": via}

    @property
    def mode(self) -> str:
        return self.endpoint or "simulator"


def _send(endpoint: str, command: dict) -> None:
    host, _, port_text = endpoint.rpartition(":")
    if not host or not port_text.isdigit():
        raise RuntimeError("The machine address must look like host:8520.")
    payload = (json.dumps(command) + "\n").encode()
    try:
        with socket.create_connection((host, int(port_text)), timeout=3) as sock:
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
    """Read pace numbers, one per line, from a radar gun on localhost."""
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
                    readings["pace_kph"] = pace
                    readings["at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
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
