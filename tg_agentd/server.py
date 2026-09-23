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
            server = await asyncio.start_unix_server(self._client, sock=sock)
        else:
            server = await asyncio.start_unix_server(self._client, path=path)
        async with server:
            await server.serve_forever()

    async def _client(self, reader, writer):
        try:
            async for line in reader:
                await self._exchange(line, writer)
        finally:
            writer.close()

    async def _exchange(self, line, writer):
        line = line.strip()
        if not line:
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError as broken:
            answer = {"ok": False, "error": f"not one JSON object per line: {broken}"}
        else:
            answer = await self._handler.handle(request)
        writer.write(json.dumps(answer).encode() + b"\n")
        await writer.drain()
