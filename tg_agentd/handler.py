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
REQUIRES = {"topics": "read", "chat-info": "read", "react": "write"}

# The name a request may give the account's saved messages in place of their identifier
SAVED = "saved"


class ChatIsNotAForum(ValueError):
    """Topics were asked of a chat that keeps none."""


class NoSuchTopic(ValueError):
    """A topic named by a request is not one the forum holds."""


# What Telegram says when a chat keeps no topics this account can list: it is no forum, it
# is no channel at all, it is closed to this account, or the identifier names nobody. The
# names are the library's own, because the request carries no other marker
NO_TOPICS = (
    "ChannelForumMissing",
    "ChannelInvalid",
    "ChannelPrivate",
    "PeerIdInvalid",
)


class BadMarkRead(ValueError):
    """A read names a message to stop at that no message can be."""


class BadFind(ValueError):
    """A search by name that names nothing to look for."""


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
        except (
            ChatIsNotAForum, NoSuchTopic, BadUpload, BadFind, BadMarkup, BadMarkRead
        ) as plain:
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
        if action == "whoami":            return _allowed(
                None, action, profile.describe(await self._client.me(), private=False)
            )

        # A caller whose call ended at its deadline has to know which silence it met: a
        # service working on a long action, one whose link to Telegram is down, or one
        # that stopped answering at all. Only this side can tell them apart, and the
        # question reads no chat and names nobody
        if action == "ping":
            return _allowed(None, action, {"telegram": await self._client.answering()})

        # Listing every chat reaches the whole account, so only the default file grants it
        if action == "dialogs":
            decision = self._store.account_may("dialogs")
            if not decision.allowed:
                return _refused(
                    decision.reason,
                    "add dialogs to 'allow: ' in chats/default.conf",
                )
            return _allowed(decision.by, action, {"chats": await self._dialogs()})

        # Looking a chat up by name reaches the chats a permission file names and the
        # members of a granting folder, and nothing else: the account's dialog list is a
        # grant of its own, and a search must not become one
        if action == "find":
            if "chat" in request or "folder" in request:
                return _refused("find looks chats up by name, and takes no chat or folder")
            return await self._find_chat(request)

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
            # A topic is known only through the forum's own list, so the name or the
            # number is turned into one once, before the first message arrives: a number
            # no topic holds would filter every message out and never say why
            if topic is not None:
                topic = getattr(await self._topic(chat, topic), "id", None)
        except permissions.BadIdentifier as bad:
            yield _refused(f"bad identifier: {bad}")
            return
        except (ChatIsNotAForum, NoSuchTopic) as missing:
            yield _refused(str(missing))
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

    async def _read_to(self, chat, request, key="id", topic=None):
        """The message a read stops at, which the chat has to hold.

        A number the chat does not hold must never reach the receipt. Telegram reads the
        number as a limit and clamps it to the newest message. A number past the end then
        marks everything read, and the unread mark of messages nobody saw is gone. The
        check runs under the grant the read is about to use, and a topic read refuses a
        number that sits in another topic
        """
        stopped = _stop_number(request.get(key))
        if stopped is None:
            return None
        held = await self._client.history(chat, ids=[stopped])
        message = held[0] if held else None
        if message is None:
            raise BadMarkRead(
                f"message {stopped} is not one chat {chat} holds, so a read cannot stop at it"
            )
        if topic is not None and _thread_of(message) != topic:
            raise BadMarkRead(
                f"message {stopped} is in no topic {topic}, so a read of that topic "
                "cannot stop at it"
            )
        return stopped

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

        Only the actions a folder may grant are looked for. Reaction writes can follow
        membership, but sending messages still needs a chat grant.
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
            listed = await self._forum_topics(chat)
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
            if "ids" in request:
                terms["ids"] = request["ids"]
            if request.get("search"):
                terms["search"] = request["search"]
            # A forum keeps its messages inside topics, and one topic is one conversation.
            # A topic is named by its number, or by the title the app shows, and either
            # way costs one request to be held to what the forum lists
            named = request.get("topic")
            if named is not None:
                terms["reply_to"] = await self._thread(chat, named)
            # Everything newer than a message already seen, which is how a watcher asks
            # for what it has not read rather than for the last N
            if request.get("since"):
                terms["min_id"] = request["since"]
            messages = await self._client.history(
                chat, limit=request.get("limit"), **terms
            )
            read = []
            for message in messages:
                answer = _message(message)
                if (answer.get("poll") or {}).get("public"):
                    await self._voters(chat, message, answer["poll"])
                read.append(answer)
            return read
        if action == "react":
            message_id = request.get("id")
            reactions = request.get("reactions")
            if type(message_id) is not int or message_id <= 0:
                raise ValueError("react needs a positive message id")
            if not isinstance(reactions, list) or any(
                not isinstance(emoji, str) or not emoji.strip() for emoji in reactions
            ):
                raise ValueError("react needs a reactions list; use [] to remove reactions")
            await self._client.react(chat, message_id, reactions)
            return None
        if action in ("send", "reply"):
            markup = await self._markup(request)
            if request.get("file") and markup == RICH:
                # Rich markup belongs to a message's text, and a caption is not one
                raise BadMarkup(
                    "rich markup cannot caption a file; send the text as its own message"
                )
            # The read that follows a reply stops at the message answered. That message
            # is the one the send names, and Telegram refuses to answer one that is not
            # there, so the receipt can never name it and no check of its own is needed
            read_to = None
            if action == "reply" and self._store.chat_flag(chat, "mark-read-on-reply"):
                read_to = _stop_number(request.get("reply_to"))
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
                await self._client.mark_read(chat, read_to)
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
            if request.get("topic") is not None:
                return await self._mark_topic(chat, request)
            stopped = await self._read_to(chat, request)
            await self._client.mark_read(chat, stopped)
            if stopped is None:
                return None
            return {"chat": chat, "read_max_id": stopped}
        if action == "media":
            return await self._download(chat, request.get("ids", []))
        raise permissions.UnknownAction(f"no handler for {action!r}")


    async def _voters(self, chat, message, poll):
        """Name who chose each answer of a poll whose voters are public.

        The counts are in the message already; the names are one more request, made only
        for such a poll, and a refusal of it leaves the counts standing rather than failing
        the whole read. One page of names is asked for, so a poll with more voters than a
        page holds says how many it did not name instead of reading as the whole list.
        """
        try:
            answer = await self._client.poll_votes(chat, message.id)
        except Exception as refused:
            poll["voters_error"] = f"{type(refused).__name__}: {refused}"
            return
        names = {
            getattr(user, "id", None): _display_name(user)
            for user in getattr(answer, "users", None) or []
        }
        votes = getattr(answer, "votes", None) or []
        # The counts are complete whether or not the page is, and the difference between
        # them is the only place a reader learns that the names are not everybody
        voted = getattr(answer, "count", None)
        if type(voted) is int and voted > len(votes):
            poll["voters_missing"] = voted - len(votes)
        by_option = {held["option"]: held for held in poll["answers"]}
        for vote in votes:
            voter = folders._identifier(getattr(vote, "peer", None))
            chosen = getattr(vote, "options", None) or [getattr(vote, "option", None)]
            for option in chosen:
                if option is None:
                    continue
                held = by_option.get(option.hex())
                if held is not None:
                    held.setdefault("voters", []).append(
                        names.get(voter) or str(voter)
                    )

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

        # A topic is one conversation inside one chat, so a folder-wide read cannot name
        # it: the request would read every member chat against another chat's topic
        if action == "mark-read" and request.get("topic") is not None:
            return _refused(
                "a topic belongs to one chat, and this request marks a whole folder; "
                "name the chat instead of the folder"
            )

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
            # Every member is checked before any of them is marked: one that does not hold
            # the message stops the whole request, as an unreachable message does for a
            # single chat, and nothing is left half marked
            for chat in members:
                await self._read_to(chat, request)
            stopped = _stop_number(request.get("id"))
            for chat in members:
                await self._client.mark_read(chat, stopped)
            marked = {"marked": members}
            if stopped is not None:
                marked["read_max_id"] = stopped
            return marked
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


    async def _find_chat(self, request):
        """The chats a grant names and lets this account read, whose name holds a text.

        Nothing here lists the account's dialogs: a chat whose only grant is a default
        file is not found, because finding it would mean reading every dialog the account
        holds, which is the grant `dialogs` exists for.
        """
        wanted = str(request.get("name") or "").strip().lstrip("@").strip()
        if not wanted:
            raise BadFind("find needs a name to look for")
        found = []
        for chat in await self._readable_chats():
            try:
                entity = await self._client.entity(chat)
            except Exception:
                # A folder can name a chat the account has left. That is not an answer,
                # and the rest of the search still is
                continue
            described = profile.describe(entity, private=False)
            # A person's name is one field and a chat's is the other; that split is the
            # profile module's business, so nothing is spelled again here
            name = described.get("name") or described.get("title") or ""
            username = described.get("username") or ""
            # A username is what people type to reach a chat, so it is searched beside the
            # title: a reader looking for a channel knows one or the other
            held = wanted.casefold()
            if held not in name.casefold() and held not in username.casefold():
                continue
            entry = {"chat": chat, "name": name}
            if username:
                entry["username"] = username
            found.append(entry)
        return _allowed(None, "find", {"chats": found})

    async def _readable_chats(self):
        """The chats a permission file names, or a granting folder holds, that may be read."""
        held = []
        for identifier, actions in self._store.survey()["chats"].items():
            if not str(identifier).lstrip("-").isdigit():
                continue
            if "read" in actions:
                held.append(int(identifier))
        for folder in await self._client.folders():
            folder_id = getattr(folder, "id", None)
            if folder_id is None or not self._store.folder_may(folder_id, "read").allowed:
                continue
            held.extend(folders.members_of(folder))
        return sorted(set(held))

    async def _forum_topics(self, chat, query=None):
        """The topics of a forum, or those whose title the query holds."""
        try:
            return await self._client.topics(chat, query=query)
        except Exception as refused:
            _keeps_no_topics(chat, refused)

    async def _topic(self, chat, wanted):
        """The forum topic a request names, by number or by the title the app shows.

        The forum is the authority on both, and neither goes through the page of the list
        alone: a number is answered by the topic it names, so a forum of thousands is read
        as surely as a short one, and a title is asked for by name. A number no topic
        holds and a title two topics share are refusals, because a guess reads another
        conversation
        """
        text = str(wanted).strip()
        if text.isdigit():
            return await self._by_number(chat, int(text))
        return await self._by_title(chat, text)

    async def _by_number(self, chat, number):
        """The topic a number names, whether or not the page of the list shows it."""
        try:
            topic = await self._client.topic(chat, number)
        except Exception as refused:
            _keeps_no_topics(chat, refused)
        if topic is None:
            raise NoSuchTopic(f"the forum holds no topic {number}")
        return topic

    async def _by_title(self, chat, text):
        """The one topic titled `text`, from the forum's own search of its titles."""
        found = [
            topic
            for topic in await self._forum_topics(chat, query=text)
            if folders.title_of(topic).casefold() == text.casefold()
        ]
        if not found:
            raise NoSuchTopic(
                f"the forum holds no topic titled {text!r}; a number names one too, and "
                "listing the topics gives both"
            )
        if len(found) > 1:
            numbers = ", ".join(str(getattr(topic, "id", None)) for topic in found)
            raise NoSuchTopic(
                f"{len(found)} topics are titled {text!r}: {numbers}; "
                "name one by its number"
            )
        return found[0]

    async def _thread(self, chat, wanted):
        """The thread a read or a subscription names, asking for a topic that is one.

        General is where the chat's own messages live, and it has no thread to read: the
        message that would start one does not exist, so the number points at whatever
        carries it, and in a bot's chat answers a library error instead.
        """
        topic = await self._topic(chat, wanted)
        identifier = getattr(topic, "id", None)
        if identifier == GENERAL_TOPIC:
            raise NoSuchTopic(
                "topic 1 is General, and it is the chat's own messages rather than a "
                "thread: read the chat without --topic, and each message says which "
                "thread it is in"
            )
        return identifier

    async def _mark_topic(self, chat, request):
        """Mark one forum topic read, and say which message the read reached.

        The General topic carries no thread of its own, so it moves with the chat's read
        position; every other topic moves by the call that names it as a thread. A
        request may name the message to stop at, which is how a reader marks what it has
        actually read rather than everything in the topic.
        """
        topic = await self._topic(chat, request["topic"])
        identifier = getattr(topic, "id", None)
        title = folders.title_of(topic)
        stopped = await self._read_to(
            chat, request, topic=None if identifier == GENERAL_TOPIC else identifier
        )
        if identifier == GENERAL_TOPIC:
            await self._client.mark_read(chat, stopped)
            answer = {
                "chat": chat,
                "topic": identifier,
                "title": title,
                "general": True,
            }
            if stopped is not None:
                answer["read_max_id"] = stopped
            return answer
        if stopped is None:
            # The topic's own top is the newest message in it, so a read to it covers
            # every message the forum counted as unread
            stopped = getattr(topic, "top_message", 0)
        if type(stopped) is not int or stopped <= 0:
            raise NoSuchTopic(f"topic {identifier} holds no message to stop the read at")
        await self._client.read_topic(chat, identifier, stopped)
        return {
            "chat": chat,
            "topic": identifier,
            "title": title,
            "read_max_id": stopped,
        }


def _stop_number(given):
    """The number a read stops at, or None when the request names none.

    A number that is not a positive integer names no message at all, so it is refused
    here. Whether the chat holds the one it does name is a question of its own
    """
    if given is None:
        return None
    if type(given) is not int or given <= 0:
        raise BadMarkRead(f"{given!r} is not a message a read can stop at")
    return given


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
# of the two true things. A round video is a video note to Telethon, and called what a
# reader sees
MEDIA_KINDS = (
    ("poll", "poll"),
    ("sticker", "sticker"),
    ("voice", "voice"),
    ("round", "video_note"),
    ("photo", "photo"),
    ("video", "video"),
    ("audio", "audio"),
    ("document", "document"),
)

# Kinds whose length is worth knowing before deciding to download them
TIMED = ("voice", "round", "video", "audio")


def _media_kind(message):
    """What a message carries, or None when it carries only words.

    Without this a sticker, a photo and a voice note are one blank line each, and a
    summary of a chat drops everything nobody typed.
    """
    for kind, attribute in MEDIA_KINDS:
        if getattr(message, attribute, None) is not None:
            return kind
    return None


def _text_of(value):
    """Plain text from a field that newer layers wrap with its entities."""
    return getattr(value, "text", value) if value is not None else ""


def _display_name(entity):
    """What a chat shows for a person or a chat, without asking the server."""
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if title:
        return title
    name = " ".join(
        part
        for part in (
            getattr(entity, "first_name", None),
            getattr(entity, "last_name", None),
        )
        if part
    )
    return name or getattr(entity, "username", None) or None


def _answered(message):
    """The message this one answers, apart from the topic it is merely posted in.

    A message in a forum topic replies to the topic itself, which is where it is rather
    than what it answers; one answering another message there names the topic as its top.
    """
    header = getattr(message, "reply_to", None)
    if header is None:
        return None
    answered = getattr(header, "reply_to_msg_id", None)
    topic = getattr(header, "reply_to_top_id", None)
    # A bot's forum names the topic as the message a post answers as well as its top, so
    # the two being one number is the place the message sits in, not an answer
    if answered is None or answered == topic:
        return None
    if getattr(header, "forum_topic", None) and not topic:
        return None
    return answered


def _forwarded(message):
    """Who a forwarded message came from: the name it carries, else the peer it names."""
    header = getattr(message, "fwd_from", None)
    if header is None:
        return None
    if getattr(header, "from_name", None):
        return header.from_name
    peer = getattr(header, "from_id", None)
    return folders._identifier(peer) if peer is not None else "hidden"


def _poll(message):
    """A poll's question, answers and counts, as the message already holds them.

    The voters' names are a separate request, so `public` says whether one can be made.
    """
    media = getattr(message, "poll", None)
    poll = getattr(media, "poll", None)
    if poll is None:
        return None
    results = getattr(media, "results", None)
    counts = {
        bytes(r.option): getattr(r, "voters", 0) or 0
        for r in (getattr(results, "results", None) or [])
    }
    return {
        "question": _text_of(getattr(poll, "question", None)),
        "answers": [
            {
                "option": bytes(a.option).hex(),
                "text": _text_of(getattr(a, "text", None)),
                "votes": counts.get(bytes(a.option), 0),
            }
            for a in getattr(poll, "answers", None) or []
        ],
        "total": getattr(results, "total_voters", 0) or 0,
        "public": bool(getattr(poll, "public_voters", False)),
        "multiple": bool(getattr(poll, "multiple_choice", False)),
        "closed": bool(getattr(poll, "closed", False)),
    }


def _message(message):
    if message is None:
        return None
    # A rich message keeps its content in blocks and its text empty, so the blocks are
    # what the text is
    content = getattr(message, "rich_message", None)
    kind = _media_kind(message)
    date = getattr(message, "date", None)
    answer = {
        "id": getattr(message, "id", None),
        # When it was written: a summary that cannot say when is a list of sentences
        "date": date.isoformat() if date is not None else None,
        "text": rich.to_markdown(content)
        if content is not None
        else getattr(message, "text", ""),
        "from": getattr(message, "sender_id", None),
        # The name the chat shows, from the sender Telethon already holds: no request
        "from_name": _display_name(getattr(message, "sender", None)),
        "out": getattr(message, "out", False),
        # The thread it is in, so a flat read of a forum is groupable by conversation
        "topic": _thread_of(message),
        "reply_to": _answered(message),
        "forwarded_from": _forwarded(message),
        "media": kind,
        # The name a document will be downloaded under, so it can be chosen by name
        "file": getattr(getattr(message, "file", None), "name", None),
        "reactions": _reactions(message),
    }
    if kind in TIMED:
        answer["duration"] = getattr(getattr(message, "file", None), "duration", None)
    if kind == "poll":
        answer["poll"] = _poll(message)
    return answer


def _reactions(message):
    result = []
    for counted in getattr(getattr(message, "reactions", None), "results", []):
        reaction = counted.reaction
        kind = type(reaction).__name__
        if kind == "ReactionEmoji":
            value = {"emoji": reaction.emoticon}
        elif kind == "ReactionCustomEmoji":
            value = {"custom_emoji_id": reaction.document_id}
        elif kind == "ReactionPaid":
            value = {"paid": True}
        else:
            continue
        result.append({
            **value,
            "count": counted.count,
            "chosen": getattr(counted, "chosen_order", None) is not None,
        })
    return result


def _allowed(by, action, result):
    return {
        "ok": True,
        "allowed_by": by,
        "wildcard": by == verbs.WILDCARD,
        "action": action,
        "result": result,
    }


# The topic a forum keeps for whatever names no other one. It is the chat's own read
# position and its own messages, not a thread of its own
GENERAL_TOPIC = 1


def _keeps_no_topics(chat, refused):
    """Raise the plain sentence for a chat that keeps no topics this account can list.

    Telegram answers a chat that is no forum, one it cannot reach and an identifier that
    names nobody with a constructor name each, and none of them is worth passing on
    """
    said = f"{type(refused).__name__}: {refused}"
    if any(marker in said for marker in NO_TOPICS):
        raise ChatIsNotAForum(f"chat {chat} keeps no topics this account can list")
    raise refused


def _thread_of(message):
    """The forum topic a message is in, or None when it is among the chat's own.

    A message posted in a topic replies to the topic itself, with no top under it; one
    answering another message names the topic as its top. General carries no header at
    all, so its messages are the chat's own.
    """
    header = getattr(message, "reply_to", None)
    if header is None:
        return None
    top = getattr(header, "reply_to_top_id", None)
    if top:
        return top
    if getattr(header, "forum_topic", None):
        return getattr(header, "reply_to_msg_id", None)
    return None


def _topic_of(message):
    """The forum topic a message is in, naming General for the chat's own messages."""
    return _thread_of(message) or GENERAL_TOPIC


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
