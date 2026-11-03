"""Talk to an agent service over its unix socket, one JSON object per line each way.

This half holds no secret and makes no decision, so the standard library is all it needs.
A client beside it imports it by name, because the directory a script runs from is already
on the import path.
"""

import json
import os
import socket
import time

# How long one call waits for its one answer. A service that takes the connection and then
# writes nothing is what this catches: without it the caller waits out a timeout somebody
# else owns, and a stuck service reads as a slow tool
DEFAULT_DEADLINE = 60.0


def timeout_from(environ=None):
    """The deadline a caller named in $TG_TIMEOUT, or None when it named none.

    A value that is not a positive number names none, because a caller shortening a long
    read and a caller writing the variable by mistake look the same here
    """
    environ = os.environ if environ is None else environ
    try:
        seconds = float((environ.get("TG_TIMEOUT") or "").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def deadline_from(environ=None):
    """The deadline for one answer in seconds: $TG_TIMEOUT when it is one, else the default."""
    return timeout_from(environ) or DEFAULT_DEADLINE


def ask(path, request, deadline=None):
    """Send one request and read the one answer to it.

    Raises TimeoutError when the service takes the connection and answers nothing before
    the deadline, which is a different fault from a socket nobody listens on
    """
    deadline = deadline_from() if deadline is None else deadline
    end = time.monotonic() + deadline
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
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
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(silence(path, deadline))
            connection.settimeout(left)
            try:
                block = connection.recv(65536)
            except TimeoutError:
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
