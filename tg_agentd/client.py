"""Wrap the Telethon client so that every visible side effect is a deliberate call.

Telethon's defaults are convenient for a chat application and wrong for an agent. Reading
is safe, but the server reflects the account as online on any request, and deleting
removes a message for everyone unless told otherwise. This wrapper exposes one method per
action the permissions name, and each states what Telethon would otherwise assume.
"""

from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    GetForumTopicsRequest,
    UpdateDialogFilterRequest,
)


class Client:
    """The account, reached through the calls the permission vocabulary allows.

    Nothing here consults the permission files. The caller decides; this class only makes
    the decision reach Telegram without picking up a default on the way.
    """

    def __init__(self, telethon):
        self._telethon = telethon

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

    async def stop(self):
        await self._telethon.disconnect()

    async def history(self, chat_id, limit=None, **kwargs):
        """Read messages from one chat.

        Reading does not mark anything read: that is a separate call in MTProto, and this
        one never makes it.
        """
        return await self._telethon.get_messages(chat_id, limit=limit, **kwargs)

    async def dialogs(self, **kwargs):
        """Every dialog the account has, unread counts included.

        The API offers no way to ask for a subset, so the caller filters. That is why the
        filtering belongs to the service and not to the agent.
        """
        return await self._telethon.get_dialogs(**kwargs)

    async def mark_read(self, chat_id, message=None):
        """Mark history as read, which the other side can see."""
        return await self._telethon.send_read_acknowledge(chat_id, message=message)

    async def send(self, chat_id, text, reply_to=None):
        """Send a message, optionally as a reply. No typing status is announced."""
        return await self._telethon.send_message(chat_id, text, reply_to=reply_to)

    async def edit(self, chat_id, message_id, text):
        """Edit a message the account itself sent."""
        return await self._telethon.edit_message(chat_id, message_id, text=text)

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
