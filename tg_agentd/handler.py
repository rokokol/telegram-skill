"""Turn one request into one answer, deciding by the permission files alone.

Every answer says what decided it. An allowed action names the granted word, and says
when that word was the wildcard, because one word standing for a whole table is a
different decision from a word naming the action. A refusal names the line that would
lift it, so the user can act on it without reading the vocabulary first.

Nothing here trusts the request. The identifier is validated before it becomes a path,
the action is looked up in the vocabulary before the account is touched, and a broken
permission file is an answer rather than a crash: the service serves other chats after it.
"""

from . import folders, permissions, verbs


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
                    "title": str(getattr(folder, "title", "")),
                    "chats": len(folders.members_of(folder)),
                }
                for folder in await self._client.folders()
            ]
            return _allowed(None, action, {"folders": listed})

        # The target decides which vocabulary applies, not the word: "read" and "media"
        # appear in both tables and mean a different reach in each
        if "folder" in request:
            return await self._folder(request, action)

        if action not in verbs.CHAT_ACTIONS:
            return _refused(f"no action is called {action!r}")

        chat = permissions.as_identifier(request.get("chat"))
        decision = self._store.chat_may(chat, action)
        if not decision.allowed:
            return _refused(decision.reason, remedy=_remedy(chat, action))

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
        return _allowed(decision.by, action, result)

    async def _perform(self, action, request):
        chat = permissions.as_identifier(request["chat"])
        if action == "read":
            # A search term goes to the server, which answers with the matches alone.
            # Filtering here would mean reading a whole history to find one message
            terms = {"search": request["search"]} if request.get("search") else {}
            messages = await self._client.history(
                chat, limit=request.get("limit"), **terms
            )
            return [_message(m) for m in messages]
        if action in ("send", "reply"):
            sent = await self._client.send(
                chat, request.get("text", ""), reply_to=request.get("reply_to")
            )
            if action == "reply" and self._store.chat_flag(chat, "mark-read-on-reply"):
                # A reply with no preceding read receipt is a sequence no person
                # produces, and it identifies the account as automated
                await self._client.mark_read(chat)
            return _message(sent)
        if action == "edit":
            await self._client.edit(chat, request["id"], request.get("text", ""))
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
                "title": str(getattr(folder, "title", "")),
                "members": members,
            }
        if action == "digest":
            return {"chats": await self._digest(members)}
        if action == "read":
            return {"members": members}
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
        marks nothing read here either.
        """
        messages = await self._client.history(chat, ids=message_ids)
        written = []
        for message in messages:
            name = getattr(message, "file_name", None) or f"{getattr(message, 'id', 0)}.bin"
            destination = self._outbox.place(f"chat-{chat}", name)
            written.append(str(await self._client.download(message, destination)))
        return written


def _message(message):
    if message is None:
        return None
    return {
        "id": getattr(message, "id", None),
        "text": getattr(message, "text", ""),
        "from": getattr(message, "sender_id", None),
        "out": getattr(message, "out", False),
    }


def _allowed(by, action, result):
    return {
        "ok": True,
        "allowed_by": by,
        "wildcard": by == verbs.WILDCARD,
        "action": action,
        "result": result,
    }


def _refused(error, remedy=None):
    answer = {"ok": False, "error": error}
    if remedy:
        answer["remedy"] = remedy
    return answer


def _remedy(chat, action):
    return f"add {action} to 'allow: ' in the permission file for chat {chat}"


def _folder_remedy(folder, action):
    return f"add {action} to 'allow: ' in the permission file for folder {folder}"
