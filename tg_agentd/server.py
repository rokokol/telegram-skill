"""Serve the handler over a unix socket, one JSON object per line.

The socket normally arrives from systemd as an inherited descriptor, so its owner and
mode are the unit's business rather than this process's: a service under DynamicUser
cannot usefully chmod a path for a named user. Binding a path is the fallback, and it is
what the suite and a manual run use.
"""

import asyncio
import json
import os
import socket

# systemd hands inherited descriptors to the service starting at 3
SD_LISTEN_FDS_START = 3

# An upload travels inside its request as base64, so a request line has to hold the
# largest file plus a third and the rest of the JSON. asyncio's default of 64 KiB would
# refuse any real document
MAX_FILE = 50 * 1024 * 1024
MAX_REQUEST = MAX_FILE * 4 // 3 + 1024 * 1024

# What a caller hears when Telegram itself is gone. Telethon raises a bare
# ConnectionError from a disconnected client, and that says nothing to an agent about
# whether the request was wrong or the link was
UNREACHABLE = "Telegram is unreachable: the service is reconnecting, ask again shortly"


def refusal():
    """The answer a caller gets while the link to Telegram is down."""
    return {"ok": False, "error": UNREACHABLE}


def _cancelled_from_outside():
    """Whether this session was cancelled by its own task rather than by a request future.

    Telethon cancels the futures of requests that were in flight when the link dropped,
    and that cancellation reaches the handler as CancelledError. The task's own
    cancellation, which is how the process shuts down, must not be turned into an answer
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def inherited_socket():
    """The socket systemd passed, or None when the process was started on its own."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    count = int(os.environ.get("LISTEN_FDS", "0"))
    if count < 1:
        return None
    return socket.socket(fileno=SD_LISTEN_FDS_START)


class Server:
    """Reads a request per line and writes an answer per line."""

    def __init__(self, handler):
        self._handler = handler

    async def serve_forever(self, *, path=None, sock=None):
        if sock is not None:
            server = await asyncio.start_unix_server(
                self._client, sock=sock, limit=MAX_REQUEST
            )
        else:
            server = await asyncio.start_unix_server(
                self._client, path=path, limit=MAX_REQUEST
            )
        async with server:
            await server.serve_forever()

    async def _client(self, reader, writer):
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    # readline raises this for one thing only: a line that outgrew the
                    # stream's limit. It clears the buffer past that line, so the
                    # connection cannot continue, but the caller gets a reason rather
                    # than an empty read. A fault anywhere else is left to speak for
                    # itself instead of arriving here as a size complaint
                    answer = {
                        "ok": False,
                        "error": f"request too large: a file may be at most {MAX_FILE} bytes",
                    }
                    writer.write(json.dumps(answer).encode() + b"\n")
                    await writer.drain()
                    return
                if not line:
                    return
                request = await self._exchange(line, writer)
                if request is not None:
                    # A subscription keeps the connection for itself until either end
                    # leaves, so nothing after it on this connection is a request
                    await self._stream(request, reader, writer)
                    return
        except ConnectionError:
            # A peer that leaves without reading its answers fails the writes still owed
            # to it, reading included. The session ends there rather than at a fault, and
            # the exception would otherwise reach the journal as a lost connection
            return
        finally:
            writer.close()

    async def _exchange(self, line, writer):
        """Answer one request, or hand back a subscription for the caller to stream."""
        line = line.strip()
        if not line:
            return None
        try:
            request = json.loads(line)
        except json.JSONDecodeError as broken:
            answer = {"ok": False, "error": f"not one JSON object per line: {broken}"}
        else:
            if isinstance(request, dict) and request.get("action") == "subscribe":
                return request
            try:
                answer = await self._handler.handle(request)
            except ConnectionError:
                answer = refusal()
            except asyncio.CancelledError:
                if _cancelled_from_outside():
                    raise
                answer = refusal()
        writer.write(json.dumps(answer).encode() + b"\n")
        await writer.drain()
        return None

    async def _stream(self, request, reader, writer):
        """Write each answer of a subscription until it ends or the reader leaves.

        A reader that closes its end is noticed at once, not at the next message: the
        subscription would otherwise hold a queue in the hub for a source nobody watches.
        """
        answers = self._handler.stream(request)
        left = asyncio.ensure_future(reader.read())
        try:
            while True:
                following = asyncio.ensure_future(anext(answers))
                done, _ = await asyncio.wait(
                    {following, left}, return_when=asyncio.FIRST_COMPLETED
                )
                if following not in done:
                    # The read under the stream has to settle before the stream is closed:
                    # closing a generator that is still running raises, and that fault
                    # reached the journal once per watcher that stopped
                    following.cancel()
                    await asyncio.gather(following, return_exceptions=True)
                    return
                try:
                    answer = following.result()
                except StopAsyncIteration:
                    return
                except (ConnectionError, asyncio.CancelledError) as gone:
                    # The stream ended because Telegram went away, not because the
                    # subscriber left: the reason is worth a line before the socket closes
                    if isinstance(gone, asyncio.CancelledError) and _cancelled_from_outside():
                        raise
                    answer = refusal()
                    writer.write(json.dumps(answer).encode() + b"\n")
                    await writer.drain()
                    return
                writer.write(json.dumps(answer).encode() + b"\n")
                await writer.drain()
        finally:
            left.cancel()
            await answers.aclose()
