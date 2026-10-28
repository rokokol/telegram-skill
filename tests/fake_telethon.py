"""A stand-in for the Telethon client that records what was asked of it.

The suite asserts on calls that must never happen — marking history read, announcing
presence, typing — so the stand-in records every call rather than only the ones a test
expects. A call this file does not define raises, because a service that reaches for an
unrecorded method is exactly what these tests exist to catch.
"""

from dataclasses import dataclass, field

# What a recorded call holds for a keyword the caller left out, so that a test can tell
# Telethon's own default from an explicit None
OMITTED = "<omitted>"


@dataclass
class User:
    """Stands in for a User, private fields included, so a test can see what is withheld."""

    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    premium: bool = False
    bot: bool = False
    # Telethon's User.phone, digits without the plus, None when hidden
    phone: str | None = None
    status: object = None
    # Not a User field: the full-profile request answers with it
    about: str | None = None


# get_me answers with the account's own User
Me = User


@dataclass
class Chat:
    """Stands in for a basic group."""

    id: int
    title: str
    participants_count: int | None = None
    about: str | None = None


@dataclass
class Channel:
    """Stands in for a channel, or a supergroup when megagroup is set."""

    id: int
    title: str
    username: str | None = None
    megagroup: bool = False
    participants_count: int | None = None
    about: str | None = None


@dataclass
class _Full:
    """What a full-profile request answers with, reduced to the fields the service reads."""

    about: str | None
    participants_count: int | None = None


@dataclass
class FakeFile:
    """Stands in for Telethon's message.file: a name only a document carries, and an ext."""

    name: str | None
    ext: str


@dataclass
class Message:
    id: int
    text: str
    sender_id: int
    out: bool = False
    # Telethon's message.file, which is None on a message with nothing attached
    file: FakeFile | None = None
    # A RichMessage, whose blocks hold the content while the text stays empty
    rich_message: object = None
    # A MessageReplyHeader, which also says which forum topic the message is in
    reply_to: object = None
    # A datetime, as Telethon gives it
    date: object = None
    # The sender Telethon resolved alongside the message: a User, Chat or Channel
    sender: object = None
    # A MessageFwdHeader on a forwarded message
    fwd_from: object = None
    # A MessageMediaPoll, whose poll and results hold the question and the counts
    poll: object = None
    # A round video, which Telethon calls a video note
    video_note: object = None


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
    pinned_peers: list = field(default_factory=list)
    exclude_read: bool = False


@dataclass
class FakeTopic:
    """Stands in for a ForumTopic."""

    id: int
    title: str
    unread_count: int = 0


class FakeClient:
    """Records calls. Every method here mirrors one the service is allowed to use."""

    def __init__(
        self,
        messages=None,
        dialogs=None,
        archived_dialogs=None,
        folders=None,
        topics=None,
        premium=False,
        full_rich=None,
        entities=None,
        poll_votes=None,
    ):
        self.calls = []
        # What a request for a poll's voters answers, by message id
        self.poll_votes = poll_votes or {}
        self.premium = premium
        # What get_entity answers for each identifier
        self.entities = entities or {}
        # The whole RichMessage of each message the server sent only in part, by id
        self.full_rich = full_rich or {}
        self.messages = messages or {}
        self.dialogs = dialogs or []
        self.archived_dialogs = archived_dialogs or []
        self.folders = folders or []
        self.topics = topics or []
        self.connected = False

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))

    # Updates. Telethon calls each registered handler with an event for every update the
    # session receives; emit plays the server's part

    def add_event_handler(self, callback, event=None):
        self._record("add_event_handler", event=type(event).__name__)
        self.handlers = getattr(self, "handlers", []) + [callback]

    async def emit(self, chat_id, message):
        for callback in getattr(self, "handlers", []):
            await callback(_NewMessage(chat_id=chat_id, message=message))

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

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        self._record("get_me")
        return Me(id=1, first_name="Self", username="self_name", premium=self.premium)

    async def get_entity(self, entity):
        self._record("get_entity", entity=entity)
        return self.entities[entity]

    async def __call__(self, request):
        # Raw requests reach the client this way. The name decides, because the service
        # builds the real Telethon request objects for everything but the status
        name = type(request).__name__
        self._record("raw", request=name, offline=getattr(request, "offline", None))
        if name == "GetDialogFiltersRequest":
            return _Filters(list(self.folders))
        if name == "GetForumTopicsRequest":
            return _Topics(list(self.topics))
        if name == "UpdateDialogFilterRequest":
            self._record(
                "raw_update_filter",
                id=getattr(request, "id", None),
                filter=getattr(request, "filter", None),
            )
        if name == "GetFullUserRequest":
            self._record("raw_full", request=name)
            return _FullAnswer(full_user=_Full(about=request.id.about))
        if name == "GetFullChannelRequest":
            self._record("raw_full", request=name)
            entity = request.channel
            return _FullAnswer(
                full_chat=_Full(entity.about, entity.participants_count)
            )
        if name == "GetFullChatRequest":
            self._record("raw_full", request=name)
            entity = next(e for e in self.entities.values() if e.id == request.chat_id)
            return _FullAnswer(full_chat=_Full(entity.about, entity.participants_count))
        if name == "GetPollVotesRequest":
            self._record("raw_poll_votes", peer=request.peer, id=request.id)
            return self.poll_votes[request.id]
        if name == "GetRichMessageRequest":
            self._record("raw_get_rich", peer=request.peer, id=request.id)
            whole = self.full_rich.get(request.id)
            return _Messages(
                [Message(id=request.id, text="", sender_id=0, rich_message=whole)]
            )
        if name in ("SendMessageRequest", "EditMessageRequest"):
            rich = getattr(request, "rich_message", None)
            reply_to = getattr(request, "reply_to", None)
            self._record(
                "raw_rich",
                request=name,
                peer=request.peer,
                message=getattr(request, "message", None),
                id=getattr(request, "id", None),
                kind=type(rich).__name__,
                markdown=getattr(rich, "markdown", None),
                reply_to=getattr(reply_to, "reply_to_msg_id", None),
            )
            return request
        return None

    async def get_input_entity(self, entity):
        self._record("get_input_entity", entity=entity)
        return entity

    def _get_response_message(self, request, result, input_chat):
        # Telethon matches the Updates a raw send returns to the message it created
        return Message(id=1001, text=request.rich_message.markdown, sender_id=1, out=True)

    # Reading

    async def get_messages(self, entity, limit=None, **kwargs):
        self._record("get_messages", entity=entity, limit=limit, **kwargs)
        held = list(self.messages.get(entity, []))
        # Asked by identifier, Telethon answers in the order asked, with None where the
        # chat holds no such message
        if kwargs.get("ids") is not None:
            by_id = {message.id: message for message in held}
            return [by_id.get(wanted) for wanted in kwargs["ids"]]
        return held[: limit or None]

    async def get_dialogs(self, **kwargs):
        self._record("get_dialogs", **kwargs)
        if kwargs.get("folder") == 1:
            return list(self.archived_dialogs)
        return list(self.dialogs)

    # Writing

    async def send_message(self, entity, message, reply_to=None, **kwargs):
        self._record(
            "send_message",
            entity=entity,
            message=message,
            reply_to=reply_to,
            parse_mode=kwargs.get("parse_mode", OMITTED),
        )
        return Message(id=999, text=message, sender_id=1, out=True)

    async def send_file(self, entity, file, caption=None, reply_to=None, **kwargs):
        self._record(
            "send_file",
            entity=entity,
            file=file,
            caption=caption,
            reply_to=reply_to,
            parse_mode=kwargs.get("parse_mode", OMITTED),
        )
        return Message(id=1000, text=caption or "", sender_id=1, out=True)

    async def edit_message(self, entity, message, text=None, **kwargs):
        self._record(
            "edit_message",
            entity=entity,
            message=message,
            text=text,
            parse_mode=kwargs.get("parse_mode", OMITTED),
        )

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
class _NewMessage:
    """What a NewMessage handler receives: the chat's marked identifier and the message."""

    chat_id: int
    message: object


@dataclass
class _FullAnswer:
    """users.UserFull or messages.ChatFull: the full profile under its own field."""

    full_user: _Full | None = None
    full_chat: _Full | None = None


@dataclass
class _Messages:
    """What a request for messages answers with: the messages, under a field of their own."""

    messages: list


@dataclass
class _Topics:
    """What GetForumTopics answers with."""

    topics: list


@dataclass
class UpdateStatusRequest:
    """Stands in for telethon.tl.functions.account.UpdateStatusRequest."""

    offline: bool = True
    seen: list = field(default_factory=list)
