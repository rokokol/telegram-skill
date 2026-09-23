"""A stand-in for the Telethon client that records what was asked of it.

The suite asserts on calls that must never happen — marking history read, announcing
presence, typing — so the stand-in records every call rather than only the ones a test
expects. A call this file does not define raises, because a service that reaches for an
unrecorded method is exactly what these tests exist to catch.
"""

from dataclasses import dataclass, field


@dataclass
class Message:
    id: int
    text: str
    sender_id: int
    out: bool = False


@dataclass
class Dialog:
    id: int
    name: str
    unread_count: int = 0


@dataclass
class FakeFolder:
    """Stands in for a DialogFilter, with members already reduced to identifiers."""

    id: int
    title: str
    include_peers: list = field(default_factory=list)
    exclude_read: bool = False


class FakeClient:
    """Records calls. Every method here mirrors one the service is allowed to use."""

    def __init__(self, messages=None, dialogs=None, folders=None):
        self.calls = []
        self.messages = messages or {}
        self.dialogs = dialogs or []
        self.folders = folders or []
        self.connected = False

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def called(self, name):
        """Every call of this name, as a list of keyword dictionaries."""
        return [kwargs for called, kwargs in self.calls if called == name]

    # Connection

    async def connect(self):
        self.connected = True
        self._record("connect")

    async def disconnect(self):
        self.connected = False
        self._record("disconnect")

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        self._record("get_me")
        return Dialog(id=1, name="self")

    async def __call__(self, request):
        # Raw requests reach the client this way. The name decides, because the service
        # builds the real Telethon request objects for everything but the status
        name = type(request).__name__
        self._record("raw", request=name, offline=getattr(request, "offline", None))
        if name == "GetDialogFiltersRequest":
            return _Filters(list(self.folders))
        if name == "UpdateDialogFilterRequest":
            self._record(
                "raw_update_filter",
                id=getattr(request, "id", None),
                filter=getattr(request, "filter", None),
            )
        return None

    # Reading

    async def get_messages(self, entity, limit=None, **kwargs):
        self._record("get_messages", entity=entity, limit=limit, **kwargs)
        return list(self.messages.get(entity, []))[: limit or None]

    async def get_dialogs(self, **kwargs):
        self._record("get_dialogs", **kwargs)
        return list(self.dialogs)

    # Writing

    async def send_message(self, entity, message, reply_to=None, **kwargs):
        self._record("send_message", entity=entity, message=message, reply_to=reply_to)
        return Message(id=999, text=message, sender_id=1, out=True)

    async def edit_message(self, entity, message, text=None, **kwargs):
        self._record("edit_message", entity=entity, message=message, text=text)

    async def delete_messages(self, entity, message_ids, revoke=None, **kwargs):
        # revoke has no default here on purpose: Telethon defaults it to True, and the
        # service must pass it explicitly every time
        self._record(
            "delete_messages", entity=entity, message_ids=message_ids, revoke=revoke
        )

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        self._record(
            "forward_messages", entity=entity, messages=messages, from_peer=from_peer
        )

    # The calls that must never happen unless a permission asked for them

    async def send_read_acknowledge(self, entity, message=None, **kwargs):
        self._record("send_read_acknowledge", entity=entity, message=message)

    async def download_media(self, message, file=None, **kwargs):
        self._record("download_media", message=message, file=file)
        return str(file)


@dataclass
class _Filters:
    """What GetDialogFilters answers with: the folders, under a field of their own."""

    filters: list


@dataclass
class UpdateStatusRequest:
    """Stands in for telethon.tl.functions.account.UpdateStatusRequest."""

    offline: bool = True
    seen: list = field(default_factory=list)
