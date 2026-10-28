"""Turn one request into one answer, deciding by the permission files alone.

Every answer says what decided it. An allowed action names the granted word, and says
when that word was the wildcard, because one word standing for a whole table is a
different decision from a word naming the action. A refusal names the line that would
lift it, so the user can act on it without reading the vocabulary first.

Nothing here trusts the request. The identifier is validated before it becomes a path,
the action is looked up in the vocabulary before the account is touched, and a broken
permission file is an answer rather than a crash: the service serves other chats after it.
"""

import base64
import binascii
import io

from . import folders, media, permissions, profile, rich, verbs
from .client import DEFAULT_MARKUP, MARKUPS, RICH
from .hub import FellBehind

# Actions that are not permissions of their own, and the permission each one takes.
# Describing a chat's other party is reading about them, so it takes what reading does
REQUIRES = {"topics": "read", "chat-info": "read"}

# The name a request may give the account's saved messages in place of their identifier
SAVED = "saved"


class ChatIsNotAForum(ValueError):
    """Topics were asked of a chat that keeps none."""


class BadUpload(ValueError):
    """A file in a request that cannot become an upload."""


class BadMarkup(ValueError):
    """Text in a request that cannot go out in the markup it names."""


class Handler:
    """Answers requests against one permission store and one account."""

    def __init__(self, store, client, outbox=None):
        self._store = store
        self._client = client
        self._outbox = outbox

    async def handle(self, request):
        try:
            return await self._dispatch(request)
        except permissions.BadIdentifier as bad:
            return _refused(f"bad identifier: {bad}")
        except permissions.UnknownAction as unknown:
            return _refused(str(unknown))
        except permissions.PermissionFileError as broken:
            return _refused(str(broken))
        except (ChatIsNotAForum, BadUpload, BadMarkup) as plain:
            return _refused(str(plain))
        except Exception as unexpected:
            # Anything else has to come back as an answer too. Letting it close the
            # connection leaves the caller with an empty read and no way to tell a failure
            # from a chat that holds nothing
            return _refused(f"{type(unexpected).__name__}: {unexpected}")

    async def _dispatch(self, request):
        action = request.get("action", "")

        # Asking what is held reaches no account and needs no grant. Without it the agent
        # can only discover its permissions by attempting actions and reading refusals
        if action == "permissions":
            return _allowed(None, action, self._store.survey())

        # Telegram shows a folder's identifier nowhere, so without this the permission
        # for a folder could never be written. The count separates a grant from a
        # mistake; the membership stays behind the grant, because that is the content
        if action == "folder-list":
            listed = [
                {
                    "id": getattr(folder, "id", None),
                    "title": folders.title_of(folder),
                    "chats": len(folders.members_of(folder)),
                }
                for folder in await self._client.folders()
                if getattr(folder, "id", None) is not None
            ]
            return _allowed(None, action, {"folders": listed})

        # The account's own profile is the user's to see, so it needs no grant
        if action == "whoami":
            return _allowed(
                None, action, profile.describe(await self._client.me(), private=False)
            )

        # Listing every chat reaches the whole account, so only the default file grants it
        if action == "dialogs":
            decision = self._store.account_may("dialogs")
            if not decision.allowed:
                return _refused(
                    decision.reason,
                    "add dialogs to 'allow: ' in chats/default.conf",
                )
            return _allowed(decision.by, action, {"chats": await self._dialogs()})

        # A subscription is a stream of answers, which the socket serves through stream
        if action == "subscribe":
            return _refused("subscribe streams, so it is served by stream, not handle")

        request = await self._saved(request)

        # The target decides which vocabulary applies, not the word: "read" and "media"
        # appear in both tables and mean a different reach in each
        if "folder" in request:
            return await self._folder(request, action)

        # Listing a forum's topics is reading its shape, so read is what it takes. It is
        # not a permission of its own: a grant nobody would withhold separately is a word
        # to remember for nothing
        needs = REQUIRES.get(action, action)
        if needs not in verbs.CHAT_ACTIONS:
            return _refused(f"no action is called {action!r}")

        chat = permissions.as_identifier(request.get("chat"))
        refusal, by, through = await self._grant(chat, needs)
        if refusal is not None:
            return refusal

        # A download has to land somewhere the user can reach. Inventing a path would put
        # the account's files wherever this process happens to be running
        if action == "media" and self._outbox is None:
            return _refused("this service has no outbox, so nothing can be downloaded")

        # forward reaches two chats, so the destination decides as much as the source
        if action == "forward":
            target = permissions.as_identifier(request.get("to"))
            allowed_there = self._store.chat_may(target, "send")
            if not allowed_there.allowed:
                return _refused(
                    allowed_there.reason, remedy=_remedy(target, "send")
                )

        result = await self._perform(action, request)
        return _with_folder(_allowed(by, action, result), through)

    async def stream(self, request):
        """Answer a subscription: one answer for the grant, then one per new message.

        The grant is decided again for every message, so withdrawing read stops the
        stream at the next message rather than at the next connection. The subscription
        is taken before the first answer, so nothing that lands in between is lost.
        """
        topic = request.get("topic")
        try:
            request = await self._saved(request)
            chat = permissions.as_identifier(request.get("chat"))
            refusal, by, through = await self._grant(chat, "read")
        except permissions.BadIdentifier as bad:
            yield _refused(f"bad identifier: {bad}")
            return
        except permissions.PermissionFileError as broken:
            yield _refused(str(broken))
            return
        if refusal is not None:
            yield refusal
            return
        subscription = self._client.hub.subscribe(chat)
        try:
            yield _with_folder(_allowed(by, "subscribe", {"chat": chat}), through)
            while True:
                try:
                    message = await subscription.next()
                except FellBehind:
                    yield _refused(
                        "this subscriber fell behind and missed messages; read from the "
                        "last one seen, then subscribe again"
                    )
                    return
                if topic is not None and _topic_of(message) != topic:
                    continue
                try:
                    refusal, _, _ = await self._grant(chat, "read")
                except permissions.PermissionFileError as broken:
                    refusal = _refused(str(broken))
                if refusal is not None:
                    yield refusal
                    return
                await self._client.complete(chat, message)
                yield {"ok": True, "message": _message(message)}
        finally:
            self._client.hub.unsubscribe(chat, subscription)

    async def _saved(self, request):
        """Saved messages are the account's chat with itself, so "saved" stands for the
        account's own identifier. The grant stays in the file named by that number."""
        for key in ("chat", "to"):
            if request.get(key) == SAVED:
                request = {**request, key: await self._client.own_id()}
        return request

    async def _grant(self, chat, needs):
        """Who allows this chat the word it needs: (None, by, through), or (refusal, ...)."""
        decision = self._store.chat_may(chat, needs)
        if decision.allowed:
            return None, decision.by, None
        # A folder granting read reaches the chats inside it. Its own file is checked
        # first, so a chat the user decided on personally keeps that decision
        through = await self._folder_covering(chat, needs)
        if through is None:
            # The remedy names the grant, which differs from the action for topics
            # and chat-info: a file naming either word would be refused as unknown
            return _refused(decision.reason, remedy=_remedy(chat, needs)), None, None
        return None, through["by"], through

    async def _folder_covering(self, chat, action):
        """A granted folder holding this chat, or None.

        Only the actions a folder may grant are looked for. A folder never lends a write
        action, whatever its own file says, because its membership moves without anyone
        here being told.
        """
        if action not in verbs.FOLDER_CONTENT_ACTIONS:
            return None
        for folder in await self._client.folders():
            folder_id = getattr(folder, "id", None)
            if folder_id is None:
                continue
            members = folders.members_of(folder)
            if chat not in members:
                continue
            decision = self._store.folder_may(folder_id, action)
            if decision.allowed:
                return {
                    "by": decision.by,
                    "id": folder_id,
                    "title": folders.title_of(folder),
                    "chats": len(members),
                }
        return None

    async def _markup(self, request):
        """The markup the request's text goes out in, checked before anything is sent.

        A custom emoji is the one entity the server keeps from an account without
        Premium, and it drops the emoji rather than refusing the message. Refusing here
        is the only way the caller learns of it.
        """
        markup = request.get("parse_mode", DEFAULT_MARKUP)
        if markup not in MARKUPS:
            raise BadMarkup(
                f"no markup is called {markup!r}; the service takes "
                + ", ".join(MARKUPS)
            )
        if self._client.custom_emoji_in(
            request.get("text"), markup
        ) and not await self._client.premium():
            raise BadMarkup(
                "the text holds a custom emoji, and only an account with Telegram "
                "Premium may send one"
            )
        return markup

    async def _perform(self, action, request):
        chat = permissions.as_identifier(request["chat"])
        if action == "chat-info":
            entity = await self._client.entity(chat)
            return profile.describe(entity, await self._client.full_profile(entity))
        if action == "topics":
            try:
                listed = await self._client.topics(chat)
            except Exception as refused:
                # A chat that is simply not a forum is an ordinary answer rather than a
                # fault, and the library's own error names a constructor nobody outside
                # it has heard of
                if "ChannelForumMissing" in str(refused):
                    raise ChatIsNotAForum(
                        f"chat {chat} is not a forum, so it has no topics"
                    ) from None
                raise
            return {
                "topics": [
                    {
                        "id": getattr(topic, "id", None),
                        "title": folders.title_of(topic),
                        "unread": getattr(topic, "unread_count", 0),
                    }
                    for topic in listed
                ]
            }
        if action == "read":
            # A search term goes to the server, which answers with the matches alone.
            # Filtering here would mean reading a whole history to find one message
            terms = {}
            if request.get("search"):
                terms["search"] = request["search"]
            # A forum keeps its messages inside topics, and one topic is one conversation
            if request.get("topic"):
                terms["reply_to"] = request["topic"]
            # Everything newer than a message already seen, which is how a watcher asks
            # for what it has not read rather than for the last N
            if request.get("since"):
                terms["min_id"] = request["since"]
            messages = await self._client.history(
                chat, limit=request.get("limit"), **terms
            )
            return [_message(m) for m in messages]
        if action in ("send", "reply"):
            markup = await self._markup(request)
            if request.get("file") and markup == RICH:
                # Rich markup belongs to a message's text, and a caption is not one
                raise BadMarkup(
                    "rich markup cannot caption a file; send the text as its own message"
                )
            # A file is a message too, so it takes the grant that sends words. Its bytes
            # arrive in the request because the service never opens a path on a caller's
            # behalf: a path would reach whatever this process can read, its session
            # included
            if request.get("file"):
                sent = await self._client.send_file(
                    chat,
                    _upload(request["file"]),
                    caption=request.get("text"),
                    reply_to=request.get("reply_to"),
                    markup=markup,
                )
            else:
                sent = await self._client.send(
                    chat,
                    request.get("text", ""),
                    reply_to=request.get("reply_to"),
                    markup=markup,
                )
            if action == "reply" and self._store.chat_flag(chat, "mark-read-on-reply"):
                # A reply with no preceding read receipt is a sequence no person
                # produces, and it identifies the account as automated
                await self._client.mark_read(chat)
            return _message(sent)
        if action == "edit":
            markup = await self._markup(request)
            await self._client.edit(
                chat, request["id"], request.get("text", ""), markup=markup
            )
            return None
        if action in ("delete", "delete-for-all"):
            await self._client.delete(
                chat, request.get("ids", []), for_all=action == "delete-for-all"
            )
            return None
        if action == "forward":
            await self._client.forward(
                permissions.as_identifier(request["to"]),
                request.get("ids", []),
                from_chat_id=chat,
            )
            return None
        if action == "mark-read":
            await self._client.mark_read(chat)
            return None
        if action == "media":
            return await self._download(chat, request.get("ids", []))
        raise permissions.UnknownAction(f"no handler for {action!r}")


    async def _folder(self, request, action):
        """Answer a folder request, reporting membership that moved since the grant."""
        folder_id = permissions.as_identifier(request.get("folder"))
        decision = self._store.folder_may(folder_id, action)
        if not decision.allowed:
            return _refused(decision.reason, remedy=_folder_remedy(folder_id, action))

        found = await self._find_folder(folder_id)
        if found is None:
            return _refused(f"the account has no folder {folder_id}")
        members = folders.members_of(found)

        result = await self._perform_folder(action, request, found, members)
        answer = _allowed(decision.by, action, result)
        moved = folders.drift(self._store.folder_field(folder_id, "members"), members)
        if moved:
            answer["warning"] = moved
        return answer

    async def _find_folder(self, folder_id):
        for folder in await self._client.folders():
            if getattr(folder, "id", None) == int(folder_id):
                return folder
        return None

    async def _perform_folder(self, action, request, folder, members):
        if action == "folder-read":
            return {
                "id": getattr(folder, "id", None),
                "title": folders.title_of(folder),
                "members": members,
            }
        if action == "digest":
            counted = await self._digest(members)
            # A folder can hold a chat that has no dialog: a contact nobody ever wrote
            # to, or one in the archive. Counting only what was found and saying nothing
            # turns a partial summary into what reads like a complete one
            return {
                "chats": counted,
                "counted": len(counted),
                "members": len(members),
            }
        if action == "read":
            return {"members": members}
        if action == "mark-read":
            for chat in members:
                await self._client.mark_read(chat)
            return {"marked": members}
        if action == "folder-edit":
            wanted = sorted(set(members) | set(request.get("add", [])))
            wanted = [m for m in wanted if m not in set(request.get("remove", []))]
            folder.include_peers = wanted
            await self._client.update_folder(getattr(folder, "id"), folder)
            return {"members": wanted}
        if action == "folder-delete":
            await self._client.update_folder(getattr(folder, "id"), None)
            return None
        raise permissions.UnknownAction(f"no handler for {action!r}")

    async def _dialogs(self):
        """Every chat the account holds, archived ones included, marking nothing read."""
        held = {}
        for archived in (False, True):
            options = {"folder": 1} if archived else {}
            for dialog in await self._client.dialogs(**options):
                identifier = getattr(dialog, "id", None)
                if identifier is None:
                    continue
                held.setdefault(
                    identifier,
                    {
                        "chat": identifier,
                        "name": getattr(dialog, "name", ""),
                        "unread": getattr(dialog, "unread_count", 0),
                        "archived": archived,
                    },
                )
        return list(held.values())

    async def _digest(self, members):
        """Unread counts for the folder's chats, read without marking anything."""
        inside = set(members)
        counted = []
        for dialog in await self._client.dialogs():
            if getattr(dialog, "id", None) in inside:
                counted.append(
                    {
                        "chat": dialog.id,
                        "name": getattr(dialog, "name", ""),
                        "unread": getattr(dialog, "unread_count", 0),
                    }
                )
        return counted

    async def _download(self, chat, message_ids):
        """Fetch the named messages and write their attachments into the outbox.

        Reading the messages to download them is the same call that reads history, so it
        marks nothing read here either. An identifier the chat does not hold, and a
        message with nothing attached, are reported apart: otherwise a request that
        downloaded nothing reads as one that succeeded.
        """
        messages = await self._client.history(chat, ids=message_ids)
        result = {"written": [], "missing": [], "no_file": []}
        for wanted, message in zip(message_ids, messages):
            if message is None:
                result["missing"].append(wanted)
                continue
            attached = getattr(message, "file", None)
            if attached is None:
                result["no_file"].append(wanted)
                continue
            # Only a document carries a name. A photo or a voice note gets its message
            # and the extension Telegram reports for it
            name = attached.name or f"{wanted}{attached.ext or ''}"
            destination = self._outbox.place(f"chat-{chat}", name)
            result["written"].append(
                str(await self._client.download(message, destination))
            )
        # An empty list would be read on every download for nothing, so only what
        # happened is named
        return {key: found for key, found in result.items() if found or key == "written"}


def _upload(attached):
    """The bytes of an attached file as a stream Telegram names after the file.

    The name comes from the caller and reaches the other side as the file name, so it is
    reduced to one path component like a download's.
    """
    try:
        data = base64.b64decode(attached.get("data", ""), validate=True)
    except (binascii.Error, ValueError):
        raise BadUpload("the attached file is not valid base64") from None
    stream = io.BytesIO(data)
    stream.name = media.component(attached.get("name", ""))
    return stream


# The kinds Telethon exposes as named attributes, most specific first: a sticker and a
# voice note are both documents, and calling either one a document says the less useful
# of the two true things
MEDIA_KINDS = ("sticker", "voice", "photo", "video", "audio", "document")


def _media_kind(message):
    """What a message carries, or None when it carries only words.

    Without this a sticker, a photo and a voice note are one blank line each, and a
    summary of a chat drops everything nobody typed.
    """
    for kind in MEDIA_KINDS:
        if getattr(message, kind, None) is not None:
            return kind
    return None


def _message(message):
    if message is None:
        return None
    # A rich message keeps its content in blocks and its text empty, so the blocks are
    # what the text is
    content = getattr(message, "rich_message", None)
    return {
        "id": getattr(message, "id", None),
        "text": rich.to_markdown(content)
        if content is not None
        else getattr(message, "text", ""),
        "from": getattr(message, "sender_id", None),
        "out": getattr(message, "out", False),
        "media": _media_kind(message),
        # The name a document will be downloaded under, so it can be chosen by name
        "file": getattr(getattr(message, "file", None), "name", None),
    }


def _allowed(by, action, result):
    return {
        "ok": True,
        "allowed_by": by,
        "wildcard": by == verbs.WILDCARD,
        "action": action,
        "result": result,
    }


# The topic a forum keeps for whatever names no other one
GENERAL_TOPIC = 1


def _topic_of(message):
    """The forum topic a message is in, read from its reply header.

    A message posted in a topic replies to the topic itself; one answering another
    message names the topic as its top. General carries no forum header at all.
    """
    header = getattr(message, "reply_to", None)
    if header is None or not getattr(header, "forum_topic", None):
        return GENERAL_TOPIC
    return getattr(header, "reply_to_top_id", None) or header.reply_to_msg_id


def _with_folder(answer, through):
    """Name the folder an answer was allowed through, when one was."""
    if through is not None:
        answer["through_folder"] = {
            "id": through["id"],
            "title": through["title"],
            "chats": through["chats"],
        }
    return answer


def _refused(error, remedy=None):
    answer = {"ok": False, "error": error}
    if remedy:
        answer["remedy"] = remedy
    return answer


def _remedy(chat, action):
    return f"add {action} to 'allow: ' in the permission file for chat {chat}"


def _folder_remedy(folder, action):
    return f"add {action} to 'allow: ' in the permission file for folder {folder}"
