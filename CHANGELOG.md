# Changelog

Kept in the shape of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), dated rather than numbered, and with no `Unreleased` section — a skill is read at whatever revision you have checked out, so whatever is on the default branch is what everyone already has, and a section for work that has landed but not shipped would never close. The rule lives in the [versioning](https://github.com/rokokol/versioning-skill) skill, which owns what has no version

## 2026-09-23

### Added

- The skill, its service and its client. An agent reaches the account through a unix socket; `tg-agentd` holds the session inside a systemd unit's namespace and decides every request by the permission files, which live where the agent cannot write them
- Per-chat permissions, as one file per target naming the words it grants. A narrow word is a separate grant rather than a consequence of a broad one: holding `read` never carries `media`, and holding `send` never carries `forward` or `mark-read`
- `allow: all`, granting a target's whole table at once for a chat with no other party in it. Every answer it permits reports that a wildcard permitted it, and a survey of what is held lists it expanded, so its reach stays visible
- Folders as a source of reading, summarising and downloading, never of writing. A folder's membership changes from any device without the service being asked, so a message sent through one could reach a chat that joined it yesterday
- The refusal that keeps a folder grant honest: a folder granting `read`, `digest` or `media` refuses `folder-edit`, `folder-create` and `folder-delete`, even when its own file lists them. Without it the agent adds a chat to a folder it may read, and reads a chat nobody granted it
- A recorded membership fingerprint on a folder permission, so an answer through a folder whose membership has moved since the grant carries that as a warning
- An outbox for downloads, with a lifetime on each file. Both the subdirectory the agent names and the file name the sender chose are reduced to one safe path component, and a destination outside the outbox is refused rather than repaired
- `tg.py permissions`, answering with every grant held and what each expands to, reaching no account and needing no grant. Without it the only way to learn a permission is to attempt an action and read the refusal
- A defect list of thirteen entries, each breaking one guard and requiring the suite to notice, run on the default branch through the tests skill's harness
- A NixOS module as `nixosModules.default`, carrying the unit the service depends on: `DynamicUser`, credentials read by systemd before the service drops its privileges, the session under `/var/lib/private`, a socket whose owner systemd sets, and a daily sweep of the outbox. It ships here because the isolation is the service's own property, not a detail each consumer reinvents
- `tg-watch.py`, which follows one chat and prints each new message as a line. It asks from the last message it saw rather than for the last few, so nothing that arrived between two rounds is lost or repeated, and it reports both sides: a message sent from a phone is as much an event as one that arrived
- Forums: `topics` lists a forum's topics, and `--topic` reads one of them on its own. Their methods live under `messages` rather than `channels`, which is why they looked unsupported
- `--since` on a read, which is how a watcher asks for what it has not seen

### Security

- Presence is switched off by an explicit call on every connection. Telegram reflects an active session's requests as presence, so a client that merely avoids announcing itself is still visible while it reads
- No path that reads history calls `send_read_acknowledge`. Marking a chat read is its own permission and its own call
- Every deletion states whether it removes the message for every participant. Telethon defaults that flag to removing it for everyone, so an omitted flag is an unrecoverable mistake
- A chat identifier is matched against a plain Telegram identifier before it becomes a path, so a request naming a path cannot choose which permission file decides it
