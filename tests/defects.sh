#!/usr/bin/env bash
# The defect list for this repository, read by the tests skill's harness vendored beside
# it:
#
#   tests/t.sh falsify -- ./tests/check.sh behaviour
#
# Each entry breaks one guard and requires the suite to notice. The CONSEQUENCE is what
# goes wrong for the user when that guard stops working; when an entry survives, that
# sentence is the report.
#
#   defect NAME FILE FIND REPLACE CONSEQUENCE [expect survived REASON | expect caught FRAGMENT]
#
# A FIND or REPLACE holding a quote is a quoted heredoc, so the line stays exactly as the
# source spells it

# Presence. The server shows the account as online on any request of an active session,
# so silence is not invisibility
defect 'presence/absent' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
        await self._telethon(UpdateStatusRequest(offline=True))
        # The session caches each entity's access hash, and an identifier cannot be
EOF
  )" \
  '        # The session caches each entity'"'"'s access hash, and an identifier cannot be' \
  'the account shows as online whenever the agent reads anything, and the user is visibly present at hours they are not'

defect 'presence/inverted' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
UpdateStatusRequest(offline=True))
        # The session caches each entity's access hash, and an identifier cannot be
EOF
  )" \
  "$(
    cat <<'EOF'
UpdateStatusRequest(offline=False))
        # The session caches each entity's access hash, and an identifier cannot be
EOF
  )" \
  'every connection announces the user as online, which is worse than the default'

# Read receipts. Reading history is invisible in MTProto until a separate call is made
defect 'read/acknowledge' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
        messages = await self._telethon.get_messages(chat_id, limit=limit, **kwargs)
EOF
  )" \
  "$(
    cat <<'EOF'
        messages = await self._telethon.get_messages(chat_id, limit=limit, **kwargs)
        await self._telethon.send_read_acknowledge(chat_id, message=None)
EOF
  )" \
  'every chat the agent reads is marked read for the far side, so correspondents see the user reading at times they were not there'

# Deletion. Telethon defaults revoke to True, so an omitted flag deletes for everyone
defect 'delete/revoke' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
            chat_id, message_ids, revoke=for_all
EOF
  )" \
  '            chat_id, message_ids, revoke=True' \
  'a deletion meant to tidy the user own view removes the message for every participant, which cannot be undone'

# The folder invariant: a folder the agent may read is a folder the agent may not fill
defect 'folder/invariant' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        if verbs.widens_reach(action) and verbs.grants_content(granted):
EOF
  )" \
  '        if False:' \
  'the agent adds any chat to a folder it may read, and reads a chat nobody granted it'

defect 'folder/drift' 'tg_agentd/folders.py' \
  "$(
    cat <<'EOF'
    if recorded == fingerprint(current):
        return None
EOF
  )" \
  "$(
    cat <<'EOF'
    return None
    if recorded == fingerprint(current):
        return None
EOF
  )" \
  'a folder that gained chats since the grant was written reads as unchanged, so the user never learns their reach widened'

defect 'folder/digest' 'tg_agentd/handler.py' \
  "$(
    cat <<'EOF'
            if getattr(dialog, "id", None) in inside:
EOF
  )" \
  '            if True:' \
  'a digest over one folder counts every chat the account has, so unread messages from chats nobody granted reach the agent'

# Permissions. The default is refusal, an unknown word is an error, and the identifier is
# checked before it becomes a path
defect 'permission/default' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        effective = self._effective(kind, identifier)
        if effective is None:
            return self._no_file(kind, identifier)
EOF
  )" \
  "$(
    cat <<'EOF'
        effective = self._effective(kind, identifier)
        if effective is None:
            return Decision(True, "read", "no file, allowed anyway")
EOF
  )" \
  'every chat with no permission file is fully readable, so the whole account is open by default'

defect 'permission/default-fallback' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        path = self.root / kind / f"{DEFAULT}.conf"
        fields = self._read_fields(path)
        if fields is None:
            return None
        return fields, path, True
EOF
  )" \
  '        return None' \
  'a target with no file of its own ignores default.conf, so a grant the user wrote for every chat or folder reaches nothing'

defect 'permission/folder-flag-from-default' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        if fields is None and kind == "chats":
            return self._read_fields(self.root / kind / f"{DEFAULT}.conf")
EOF
  )" \
  "$(
    cat <<'EOF'
        if fields is None:
            return self._read_fields(self.root / kind / f"{DEFAULT}.conf")
EOF
  )" \
  'a folder takes its membership fingerprint from the default file, so a drift warning compares one folder with another'

defect 'account/dialogs' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        if action in granted:
            return Decision(True, action, f"the default chat grant allows {action}")
EOF
  )" \
  "$(
    cat <<'EOF'
        if True:
            return Decision(True, action, f"the default chat grant allows {action}")
EOF
  )" \
  'the whole chat list is readable without the default file granting dialogs, so the account map leaks'

defect 'permission/unknown-word' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
        unknown = verbs.unknown_words(words, table)
EOF
  )" \
  '        unknown = []' \
  'a typo in a permission file is ignored rather than reported, so a grant the user believes they wrote is silently absent'

defect 'permission/identifier' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
    if not IDENTIFIER.match(name):
        raise BadIdentifier(f"{name!r} is not a Telegram identifier")
EOF
  )" \
  '    pass' \
  'a chat identifier holding a path reads a permission file from anywhere on the filesystem, so the agent chooses its own permissions'

defect 'permission/wildcard-table' 'tg_agentd/verbs.py' \
  "$(
    cat <<'EOF'
    if WILDCARD in words and action in table:
EOF
  )" \
  '    if WILDCARD in words:' \
  'a wildcard grant permits an action no table defines, so a request naming anything at all goes through'

# What the first live run found. Each of these passed the suite and failed against a real
# account, so the stand-in now carries them
defect 'live/identifier-type' 'tg_agentd/permissions.py' \
  "$(
    cat <<'EOF'
    return int(name)
EOF
  )" \
  '    return name' \
  'a chat reaches the account as a string, which Telethon resolves as a username or a phone number, so every request names a stranger'

defect 'live/entity-cache' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
        await self._telethon.get_dialogs()
EOF
  )" \
  '        pass' \
  'a fresh session never fills its entity cache, so a chat named by identifier cannot be resolved at all'

defect 'live/unexpected-failure' 'tg_agentd/handler.py' \
  "$(
    cat <<'EOF'
        except Exception as unexpected:
EOF
  )" \
  '        except permissions.BadIdentifier as unexpected:' \
  'an unexpected failure closes the connection instead of answering, so the caller reads an empty result and cannot tell a failure from an empty chat'

# Media. Both the subdirectory and the attachment name are chosen outside this service
defect 'media/destination' 'tg_agentd/media.py' \
  "$(
    cat <<'EOF'
        if not safe_group or safe_group != str(group):
            raise BadDestination(f"{group!r} does not name a directory inside the outbox")
EOF
  )" \
  '        safe_group = str(group)' \
  'a download writes outside the outbox, so an attachment lands anywhere the service can write'

defect 'media/component' 'tg_agentd/media.py' \
  'character if character.isalnum() or character in SAFE else "-"' \
  'character' \
  'a file name keeps its path separators and control characters, so it can name a place outside its directory'

defect 'media/missing' 'tg_agentd/handler.py' \
  "$(
    cat <<'EOF'
            if message is None:
                result["missing"].append(wanted)
                continue
EOF
  )" \
  '            pass' \
  'a message the chat does not hold reads as a message with nothing attached, so a mistyped identifier is never noticed'

# Uploads. The name and the bytes come from the caller, and the request is as long as
# the file
defect 'upload/name' 'tg_agentd/handler.py' \
  '    stream.name = media.component(attached.get("name", ""))' \
  '    stream.name = attached.get("name", "")' \
  'a caller-chosen name with a path in it reaches the other side as the file name'

defect 'upload/base64' 'tg_agentd/handler.py' \
  'base64.b64decode(attached.get("data", ""), validate=True)' \
  'base64.b64decode(attached.get("data", ""))' \
  'a corrupted upload is sent as whatever bytes survive decoding, instead of being refused'

defect 'upload/limit' 'tg_agentd/server.py' \
  "$(
    cat <<'EOF'
        except ValueError:
EOF
  )" \
  '        except KeyError:' \
  'a file over the limit closes the connection with no answer, so the caller cannot tell a refusal from a crash'

# Forwarding reaches two chats, so the destination decides as much as the source
defect 'forward/destination' 'tg_agentd/handler.py' \
  "$(
    cat <<'EOF'
            allowed_there = self._store.chat_may(target, "send")
            if not allowed_there.allowed:
EOF
  )" \
  "$(
    cat <<'EOF'
            allowed_there = self._store.chat_may(target, "send")
            if False:
EOF
  )" \
  'a forward permission on one chat writes into any other chat, so content leaves a granted chat for one nobody granted'

defect 'folder/pinned' 'tg_agentd/folders.py' \
  "$(
    cat <<'EOF'
    for field in ("pinned_peers", "include_peers"):
EOF
  )" \
  '    for field in ("include_peers",):' \
  'a chat pinned inside a folder is not counted as a member, so the grant on that folder never reaches the chats the user pinned there'

defect 'folder/marked-id' 'tg_agentd/folders.py' \
  "$(
    cat <<'EOF'
        return -(CHANNEL_MARK + value)
EOF
  )" \
  '        return value' \
  'a channel listed in a folder is addressed by its raw identifier, which names a user that does not exist, so no grant through that folder reaches a channel'

# Markup. Telethon parses on this side, and an omitted mode means its client default
defect 'markup/none' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
PARSED = {"md": "md", "html": "html", "none": None}
EOF
  )" \
  "$(
    cat <<'EOF'
PARSED = {"md": "md", "html": "html", "none": "md"}
EOF
  )" \
  'text the agent asked to send literally is parsed as Markdown, so asterisks and underscores in it vanish or turn into formatting'

defect 'markup/unstated' 'tg_agentd/client.py' \
  'chat_id, text, reply_to=reply_to, parse_mode=PARSED[markup]' \
  'chat_id, text, reply_to=reply_to' \
  'the markup named in a request is ignored, so a message goes out in whatever Telethon defaults to'

defect 'markup/premium' 'tg_agentd/handler.py' \
  ') and not await self._client.premium():' \
  ') and False:' \
  'a custom emoji from an account without Premium is sent, and the server drops it from the message without a word'

# Rich messages. The server parses them, so the text has to reach it as written
defect 'rich/send' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
        if markup == RICH:
            peer = await self._telethon.get_input_entity(chat_id)
            return await self._rich(
                peer,
                SendMessageRequest(
EOF
  )" \
  "$(
    cat <<'EOF'
        if False:
            peer = await self._telethon.get_input_entity(chat_id)
            return await self._rich(
                peer,
                SendMessageRequest(
EOF
  )" \
  'a rich message goes out through the client-side parser, so its tables and formulas arrive as raw Markdown'

defect 'rich/caption' 'tg_agentd/handler.py' \
  'if request.get("file") and markup == RICH:' \
  'if False:' \
  'a rich caption fails deep in the client with an error that names a dictionary key instead of the reason'

defect 'rich/read' 'tg_agentd/handler.py' \
  "$(
    cat <<'EOF'
        if content is not None
        else getattr(message, "text", ""),
EOF
  )" \
  "$(
    cat <<'EOF'
        if False
        else getattr(message, "text", ""),
EOF
  )" \
  'a rich message reads as empty, so the agent answers a table or a formula it never saw'

defect 'rich/partial' 'tg_agentd/client.py' \
  'if content is not None and getattr(content, "part", None):' \
  'if False:' \
  'a long rich message reads as its first part only, and the agent takes the fragment for the whole'

# The watcher. It runs under hooks, so its mark and its filter decide what the agent sees
defect 'watch/echo-all' 'tg-watch.py' \
  "$(
    cat <<'EOF'
    if not (message.get("out") and message["id"] in tg.sent_ids(options.chat)):
EOF
  )" \
  "$(
    cat <<'EOF'
    if not message.get("out"):
EOF
  )" \
  'what the user writes from a phone disappears from the watch along with the agent'\''s own sends'

defect 'watch/echo-none' 'tg-watch.py' \
  "$(
    cat <<'EOF'
    if not (message.get("out") and message["id"] in tg.sent_ids(options.chat)):
EOF
  )" \
  '    if True:' \
  'every message the agent sends comes back to it as news, and it may answer itself'

defect 'watch/state' 'tg-watch.py' \
  "$(
    cat <<'EOF'
            since = round_once(options, since)
            if options.state:
                write_mark(options.state, since)
            return 0
EOF
  )" \
  "$(
    cat <<'EOF'
            since = round_once(options, since)
            return 0
EOF
  )" \
  'a watcher started again by a hook never moves its mark, so it reports the same messages on every run'

defect 'watch/state-pushed' 'tg-watch.py' \
  '        write_mark(options.state, message["id"])' \
  '        pass' \
  'a watcher stopped while following a stream resumes from before the messages it already showed, and shows them again'

defect 'watch/dedupe' 'tg-watch.py' \
  'if since is not None and message["id"] <= since:' \
  'if False:' \
  'a message that lands during the catch-up read is shown twice, once read and once pushed'

defect 'watch/restart' 'tg-watch.py' \
  "$(
    cat <<'EOF'
        print(f"tg-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return since, False
EOF
  )" \
  '        raise' \
  'a watcher ends for good whenever the service restarts, as it does on every system rebuild'

defect 'watch/wait' 'tg-watch.py' \
  '            if not now:' \
  '            if True:' \
  'after every restart of the service the watch stays deaf for the whole interval, and a reply in that gap arrives late'

defect 'push/topic' 'tg_agentd/handler.py' \
  'if topic is not None and _topic_of(message) != topic:' \
  'if False:' \
  'a watch on one forum topic shows every topic of the forum, and the agent answers conversations it was not asked to follow'

# Push. The session receives every update, so a subscription has to be held to its grant
defect 'push/recheck' 'tg_agentd/handler.py' \
  '                    refusal, _, _ = await self._grant(chat, "read")' \
  '                    refusal = None' \
  'withdrawing read from a chat does not stop a running subscription, so messages keep reaching the agent after the user took the grant back'

defect 'push/chat' 'tg_agentd/hub.py' \
  'for subscription in list(self._by_key.get(key, ())):' \
  'for subscription in [s for held in self._by_key.values() for s in held]:' \
  'a subscriber to one chat receives the messages of every chat on the account, granted or not'

defect 'push/unsubscribe' 'tg_agentd/handler.py' \
  '            self._client.hub.unsubscribe(chat, subscription)' \
  '            pass' \
  'every subscription that ends leaves its queue in the hub, so the service holds the messages of watchers long gone'

defect 'push/leave' 'tg_agentd/server.py' \
  'left = asyncio.ensure_future(reader.read())' \
  'left = asyncio.ensure_future(asyncio.sleep(3600))' \
  'a watcher that closes its connection stays subscribed until the next message in that chat, which may never come'

defect 'push/bound' 'tg_agentd/hub.py' \
  'if len(self._waiting) >= self._depth:' \
  'if False:' \
  'a subscriber that stops reading makes the service hold every message of that chat in memory, without limit'

# Profiles. The other party of a chat is described under the grant that reads the chat
defect 'info/grant' 'tg_agentd/handler.py' \
  '"chat-info": "read"' \
  '"chat-info": "send"' \
  'a chat the agent may only write into tells it the phone number, the bio and the last-seen time of the person there'

defect 'info/remedy' 'tg_agentd/handler.py' \
  'return _refused(decision.reason, remedy=_remedy(chat, needs))' \
  'return _refused(decision.reason, remedy=_remedy(chat, action))' \
  'a refused chat-info tells the user to grant a word the permission file rejects, so following the remedy breaks the file'

defect 'info/hidden' 'tg_agentd/profile.py' \
  'return {key: value for key, value in described.items() if value is not None}' \
  'return described' \
  'a phone number a person hides reads as a field that exists and is empty, rather than as one the account cannot see'

# Recovery. The link to Telegram can go away for hours, and Telethon's own reconnect is
# what turned a throttle into a storm
defect 'recovery/unpaused' 'tg_agentd/client.py' \
  '                pause = min(RETRY_PAUSE * 2 ** (failures - 1), RETRY_CAP)' \
  '                pause = 0' \
  'the service retries Telegram with no pause after every failure, which is the burst that made the link answer a throttling page'

defect 'recovery/attempt' 'tg_agentd/client.py' \
  '                await self.reconnect()' \
  '                raise ConnectionError("refused")' \
  'a dropped link is never picked up again, and every request answers "unreachable" until someone restarts the unit by hand'

defect 'recovery/give-up' 'tg_agentd/__main__.py' \
  '            raise SystemExit(os.EX_TEMPFAIL)' \
  '            raise SystemExit(0)' \
  'an outage that outlives the budget ends the service successfully, so systemd never restarts it and the agent stays deaf'

defect 'recovery/telethon-retry' 'tg_agentd/__main__.py' \
  '        auto_reconnect=False,' \
  '        auto_reconnect=True,' \
  'Telethon retries a throttling answer with no pause and then gives up for good, which is the deaf-but-alive service this loop exists to end'

defect 'recovery/silent' 'tg_agentd/server.py' \
  "$(
    cat <<'SILENT'
            except ConnectionError:
                answer = refusal()
SILENT
  )" \
  '            except FileNotFoundError:
                answer = refusal()' \
  'a request made while the link is down gets no answer at all, so the caller cannot tell a lost link from a request the service never read'
