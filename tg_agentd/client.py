"""Wrap the Telethon client so that every visible side effect is a deliberate call.

Telethon's defaults are convenient for a chat application and wrong for an agent. Reading
is safe, but the server reflects the account as online on any request, and deleting
removes a message for everyone unless told otherwise. This wrapper exposes one method per
action the permissions name, and each states what Telethon would otherwise assume.
"""

import asyncio

from telethon import events, utils
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import (
    EditMessageRequest,
    GetDialogFiltersRequest,
    GetForumTopicsRequest,
    GetFullChatRequest,
    GetRichMessageRequest,
    SendMessageRequest,
    SendReactionRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    InputReplyToMessage,
    InputRichMessageMarkdown,
    MessageEntityCustomEmoji,
    ReactionEmoji,
)

from .hub import Hub

# The markups Telethon parses on this side, and what it is told for each. The server
# receives only entities, so it never sees which of these was used. "none" is an explicit
# None: an omitted mode means Telethon's client-wide default
PARSED = {"md": "md", "html": "html", "none": None}

# Rich Markdown is parsed by the server instead: headings, tables, lists and formulas,
# which entities cannot express. The high-level send_message has no way to carry it
RICH = "rich"

MARKUPS = (*PARSED, RICH)
DEFAULT_MARKUP = "md"

# How often the link is looked at, and how slowly the service comes back. The pause starts
# at RETRY_PAUSE and doubles up to RETRY_CAP, so a long outage costs a handful of
# connections rather than the burst that made Telegram answer a throttling page in the
# first place. Telethon's own reconnect is off for the same reason: it retries a
# throttling answer with no pause at all and then stops for good
WATCH_INTERVAL = 1
RETRY_PAUSE = 2
RETRY_CAP = 60
GIVE_UP_AFTER = 8


class Client:
    """The account, reached through the calls the permission vocabulary allows.

    Nothing here consults the permission files. The caller decides; this class only makes
    the decision reach Telegram without picking up a default on the way.
    """

    def __init__(self, telethon):
        self._telethon = telethon
        self._own_id = None
        self.hub = Hub()

    async def start(self):
        """Connect, then say the account is away.

        Staying quiet is not enough. The server treats an active session's requests as
        activity and shows the account as online to everyone allowed to see it.
        """
        await self._telethon.connect()
        await self._telethon(UpdateStatusRequest(offline=True))
        # The session caches each entity's access hash, and an identifier cannot be
        # resolved without it. A fresh session has an empty cache, so a chat named by
        # number reaches the account as a stranger until one pass has filled it
        await self._telethon.get_dialogs()
        # Updates arrive on the open session at no cost of their own, so subscribers are
        # told of a message as it lands. Telethon reports both directions by default
        self._telethon.add_event_handler(self._arrived, events.NewMessage())

    async def _arrived(self, event):
        self.hub.publish(event.chat_id, event.message)

    async def stop(self):
        await self._telethon.disconnect()

    def connected(self):
        """Whether Telegram is reachable through this session right now."""
        return self._telethon.is_connected()

    async def reconnect(self):
        """Join Telegram again, and say the account is away once more.

        A session that arrives is a session that shows its requests as activity, so the
        offline status has to be sent again on the new connection
        """
        await self._telethon.connect()
        await self._telethon(UpdateStatusRequest(offline=True))

    async def watch(self):
        """Keep the link up, and give up once the outage outlives the budget.

        False means the service could not reach Telegram for the whole budget. The caller
        ends the process on it: a restart is the only thing left that systemd can see, and
        RestartSec becomes the pause before the next attempt
        """
        failures = 0
        while True:
            await asyncio.sleep(WATCH_INTERVAL)
            if self.connected():
                failures = 0
                continue
            try:
                await self.reconnect()
            except Exception:
                failures += 1
                if failures >= GIVE_UP_AFTER:
                    return False
                pause = min(RETRY_PAUSE * 2 ** (failures - 1), RETRY_CAP)
                await asyncio.sleep(pause)
            else:
                failures = 0

    async def own_id(self):
        """The account's identifier, which is also the identifier of its saved messages."""
        if self._own_id is None:
            self._own_id = (await self._telethon.get_me()).id
        return self._own_id

    async def history(self, chat_id, limit=None, **kwargs):
        """Read messages from one chat.

        Reading does not mark anything read: that is a separate call in MTProto, and this
        one never makes it. A rich message the server sent only in part is fetched whole,
        so a reader never takes a fragment for the message.
        """
        messages = await self._telethon.get_messages(chat_id, limit=limit, **kwargs)
        for message in messages:
            await self.complete(chat_id, message)
        return messages

    async def complete(self, chat_id, message):
        """Fetch the rest of a rich message the server sent only in part."""
        content = getattr(message, "rich_message", None)
        if content is not None and getattr(content, "part", None):
            answer = await self._telethon(GetRichMessageRequest(peer=chat_id, id=message.id))
            whole = [m for m in getattr(answer, "messages", []) if m.id == message.id]
            if whole and whole[0].rich_message is not None:
                message.rich_message = whole[0].rich_message
        return message

    async def dialogs(self, **kwargs):
        """Every dialog the account has, unread counts included.

        The API offers no way to ask for a subset, so the caller filters. That is why the
        filtering belongs to the service and not to the agent.
        """
        return await self._telethon.get_dialogs(**kwargs)

    async def mark_read(self, chat_id, message=None):
        """Mark history as read, which the other side can see."""
        return await self._telethon.send_read_acknowledge(chat_id, message=message)

    async def react(self, chat_id, message_id, reactions):
        return await self._telethon(
            SendReactionRequest(
                peer=chat_id,
                msg_id=message_id,
                reaction=[ReactionEmoji(emoji) for emoji in reactions],
            )
        )

    async def me(self):
        """The account's own User."""
        return await self._telethon.get_me()

    async def entity(self, chat_id):
        """The user, group or channel behind a chat identifier."""
        return await self._telethon.get_entity(chat_id)

    async def full_profile(self, entity):
        """The part of a profile the entity itself leaves out: the bio, and a group's size.

        Each kind of entity has its own request for it, and an unknown kind has none.
        """
        kind = type(entity).__name__
        if kind == "User":
            return (await self._telethon(GetFullUserRequest(id=entity))).full_user
        if kind == "Channel":
            return (await self._telethon(GetFullChannelRequest(channel=entity))).full_chat
        if kind == "Chat":
            return (await self._telethon(GetFullChatRequest(chat_id=entity.id))).full_chat
        return None

    async def premium(self):
        """Whether the account has Premium, asked fresh because a subscription can lapse."""
        return bool(getattr(await self._telethon.get_me(), "premium", False))

    def custom_emoji_in(self, text, markup=DEFAULT_MARKUP):
        """Whether the text, read in this markup, holds a custom emoji.

        The parse is Telethon's own and makes no request, so the answer matches what a
        send would put on the wire.
        """
        if markup == RICH:
            # The server parses rich text and holds its own rules for what it accepts
            return False
        mode = utils.sanitize_parse_mode(PARSED[markup])
        if mode is None or not text:
            return False
        _, entities = mode.parse(text)
        return any(isinstance(entity, MessageEntityCustomEmoji) for entity in entities)

    async def send(self, chat_id, text, reply_to=None, markup=DEFAULT_MARKUP):
        """Send a message, optionally as a reply. No typing status is announced."""
        if markup == RICH:
            peer = await self._telethon.get_input_entity(chat_id)
            return await self._rich(
                peer,
                SendMessageRequest(
                    peer=peer,
                    message="",
                    rich_message=InputRichMessageMarkdown(markdown=text),
                    reply_to=None
                    if reply_to is None
                    else InputReplyToMessage(reply_to_msg_id=reply_to),
                ),
            )
        return await self._telethon.send_message(
            chat_id, text, reply_to=reply_to, parse_mode=PARSED[markup]
        )

    async def _rich(self, peer, request):
        """Send a raw request and find the message it made in the updates it returns.

        The lookup is Telethon's own private helper, which its send_message uses for the
        same job. Writing a second one would match updates by different rules.
        """
        result = await self._telethon(request)
        return self._telethon._get_response_message(request, result, peer)

    async def send_file(
        self, chat_id, file, caption=None, reply_to=None, markup=DEFAULT_MARKUP
    ):
        """Send a file, with the text as its caption. No upload status is announced."""
        return await self._telethon.send_file(
            chat_id,
            file,
            caption=caption,
            reply_to=reply_to,
            parse_mode=PARSED[markup],
        )

    async def edit(self, chat_id, message_id, text, markup=DEFAULT_MARKUP):
        """Edit a message the account itself sent."""
        if markup == RICH:
            peer = await self._telethon.get_input_entity(chat_id)
            return await self._rich(
                peer,
                EditMessageRequest(
                    peer=peer,
                    id=message_id,
                    rich_message=InputRichMessageMarkdown(markdown=text),
                ),
            )
        return await self._telethon.edit_message(
            chat_id, message_id, text=text, parse_mode=PARSED[markup]
        )

    async def delete(self, chat_id, message_ids, *, for_all):
        """Delete messages, for this account alone or for every participant.

        `for_all` has no default. Telethon's `revoke` defaults to True, so a path that
        forgets the flag deletes the message for everyone, which cannot be undone.
        """
        return await self._telethon.delete_messages(
            chat_id, message_ids, revoke=for_all
        )

    async def forward(self, to_chat_id, message_ids, from_chat_id):
        """Forward messages, which carries the original author to the destination."""
        return await self._telethon.forward_messages(
            to_chat_id, message_ids, from_peer=from_chat_id
        )

    async def folders(self):
        """Every folder the account has.

        Telethon offers no high-level call for these, so the raw request is the interface.
        `edit_folder` is a different thing: it moves a chat into the archive.
        """
        answer = await self._telethon(GetDialogFiltersRequest())
        return list(getattr(answer, "filters", answer) or [])

    async def topics(self, chat_id, limit=100):
        """The topics of a forum.

        A forum keeps every message inside a topic, so reading one without naming a topic
        returns all of them interleaved. These live under `messages` rather than
        `channels`, which is where the name suggests they would be.
        """
        answer = await self._telethon(
            GetForumTopicsRequest(
                peer=chat_id,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=limit,
            )
        )
        return list(getattr(answer, "topics", answer) or [])

    async def update_folder(self, folder_id, folder):
        """Replace a folder, or delete it when given nothing to put there."""
        return await self._telethon(UpdateDialogFilterRequest(folder_id, folder))

    async def download(self, message, destination):
        """Download a message's media to a path the caller has already validated."""
        return await self._telethon.download_media(message, file=destination)
