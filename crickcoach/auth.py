"""Coach sign-in for a VANSH that faces the internet.

Set VANSH_COACH_KEY to require it. The coach signs in once with the key and
gets a signed cookie that lasts 30 days. Scripts at the net, such as the edge
recorder, send the key as a bearer token instead.

Without the key VANSH is open. That suits a laptop on the net's own network.
It does not suit vansh.best: sessions hold video of players, and the machine
queue drives a bowling machine.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

COOKIE = "vansh_coach"
DAYS = 30
WINDOW_S = 600
ATTEMPTS = 8


class Auth:
    def __init__(self, key: str | None, secret: bytes):
        self.key = (key or "").strip() or None
        # Changing the key signs everyone out.
        self._signing = hmac.new(secret, (self.key or "").encode(), hashlib.sha256).digest()
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def required(self) -> bool:
        return self.key is not None

    def check_key(self, given: str | None) -> bool:
        if not self.required or not given:
            return False
        return hmac.compare_digest(given.strip().encode(), self.key.encode())

    def token(self) -> str:
        expires = int(time.time()) + DAYS * 86400
        body = f"{expires}.{secrets.token_hex(8)}"
        return f"{body}.{self._sign(body)}"

    def valid(self, token: str | None) -> bool:
        if not self.required:
            return True
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3:
            return False
        expires, nonce, signature = parts
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._sign(f"{expires}.{nonce}"))

    def allow_attempt(self, client: str) -> bool:
        """At most a few wrong keys per address in ten minutes."""
        now = time.time()
        with self._lock:
            recent = [moment for moment in self._attempts.get(client, []) if now - moment < WINDOW_S]
            if len(recent) >= ATTEMPTS:
                self._attempts[client] = recent
                return False
            recent.append(now)
            self._attempts[client] = recent
            if len(self._attempts) > 5000:
                self._attempts = {k: v for k, v in self._attempts.items() if v and now - v[-1] < WINDOW_S}
            return True

    def forget(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def _sign(self, body: str) -> str:
        return hmac.new(self._signing, body.encode(), hashlib.sha256).hexdigest()
