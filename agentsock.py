"""Talk to an agent service over its unix socket, one JSON object per line each way.

This half holds no secret and makes no decision, so the standard library is all it needs.
A client beside it imports it by name, because the directory a script runs from is already
on the import path.
"""

import json
import os
import socket
import time

# This file is vendored into other skills, so it carries the mechanism and no policy: how
# long a call may wait is the caller's decision, and a skill that wants a deadline names
# one. A caller that names none waits as long as it takes, which is what every caller did
# before the deadline existed


def ask(path, request, deadline=None):
    """Send one request and read the one answer to it.

    `deadline` is how many seconds the answer may take, and None waits indefinitely. A
    call that outlives it raises TimeoutError naming the socket and the seconds, which is
    a different fault from a socket nobody listens on: the service is there and silent
    """
    end = None if deadline is None else time.monotonic() + deadline
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        if deadline is not None:
            connection.settimeout(deadline)
        connection.connect(path)
        try:
            connection.sendall(json.dumps(request).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            # The service stops reading a request that outgrows its limit and answers
            # at once, so the answer may already be waiting behind the broken send
            pass
        answer = b""
        while not answer.endswith(b"\n"):
            if end is not None:
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError(silence(path, deadline))
                connection.settimeout(left)
            try:
                block = connection.recv(65536)
            except TimeoutError:
                if deadline is None:
                    raise
                raise TimeoutError(silence(path, deadline)) from None
            if not block:
                break
            answer += block
    return json.loads(answer.decode())


def silence(path, deadline):
    """What a caller is told when the service holds the connection and writes nothing."""
    return f"{path} took the connection and answered nothing in {deadline:g}s"


def stream(path, request):
    """Send one request and yield every answer line until the service closes the stream.

    No deadline here: a subscriber waits for the next message by design, and the opening
    answer is the service's to send. A watcher that hears nothing at all is a stalled
    subscriber, not a fault with an answer to report
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as lines:
            for line in lines:
                if line.strip():
                    yield json.loads(line.decode())


class SentLog:
    """What this client sent, one "KEY ID" per line, newest last.

    A watcher reads it to tell the agent's own sends from what the user sent elsewhere,
    because the server marks both as the account's. Each service keeps its own file, so
    an identifier from one never hides a message of another.
    """

    KEPT = 1000

    def __init__(self, variable, service):
        self._variable = variable
        self._service = service

    def path(self):
        if os.environ.get(self._variable):
            return os.environ[self._variable]
        state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        return os.path.join(state, self._service, "sent")

    def remember(self, key, identifier):
        path = self.path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as kept:
                lines = kept.read().splitlines()
        except FileNotFoundError:
            lines = []
        lines = (lines + [f"{key} {identifier}"])[-self.KEPT :]
        with open(path, "w", encoding="utf-8") as kept:
            kept.write("\n".join(lines) + "\n")

    def ids(self, key):
        """The identifiers sent under one key, as text."""
        try:
            with open(self.path(), encoding="utf-8") as kept:
                pairs = [line.split() for line in kept]
        except FileNotFoundError:
            return set()
        return {pair[1] for pair in pairs if len(pair) == 2 and pair[0] == str(key)}
