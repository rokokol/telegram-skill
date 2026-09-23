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
EOF
  )" \
  '        pass' \
  'the account shows as online whenever the agent reads anything, and the user is visibly present at hours they are not'

defect 'presence/inverted' 'tg_agentd/client.py' \
  'UpdateStatusRequest(offline=True)' \
  'UpdateStatusRequest(offline=False)' \
  'every connection announces the user as online, which is worse than the default'

# Read receipts. Reading history is invisible in MTProto until a separate call is made
defect 'read/acknowledge' 'tg_agentd/client.py' \
  "$(
    cat <<'EOF'
        return await self._telethon.get_messages(chat_id, limit=limit, **kwargs)
EOF
  )" \
  "$(
    cat <<'EOF'
        found = await self._telethon.get_messages(chat_id, limit=limit, **kwargs)
        await self._telethon.send_read_acknowledge(chat_id, message=None)
        return found
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
        if granted is None:
            return self._no_file(kind, identifier)
        return self._weigh(granted, action, table, kind.rstrip("s"), identifier)
EOF
  )" \
  "$(
    cat <<'EOF'
        if granted is None:
            return Decision(True, "read", "no file, allowed anyway")
        return self._weigh(granted, action, table, kind.rstrip("s"), identifier)
EOF
  )" \
  'every chat with no permission file is fully readable, so the whole account is open by default'

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
