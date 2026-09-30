# Changelog

Kept in the shape of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), dated rather than numbered, and with no `Unreleased` section — a skill is read at whatever revision you have checked out, so whatever is on the default branch is what everyone already has, and a section for work that has landed but not shipped would never close. The rule lives in the [versioning](https://github.com/rokokol/versioning-skill) skill, which owns what has no version

## 2026-09-30

### Added

- A NixOS VM test of the module, `checks.<system>.vm`, run in CI. It boots the unit with a probe in place of the service and holds four properties: a download is readable by a person, the session is not, the service cannot write its permissions, and only the socket user reaches the socket
- An assertion that `permissionsDir` and `outboxDir` lie outside the service's StateDirectory
- Sending a file with `send` or `reply` and `tg.py --file`, under the same grant as a message. The client reads the file and puts its bytes into the request; the service never opens a path. A file may be up to 50 MiB, and a larger one is refused with an answer rather than a closed connection

### Changed

- `permissionsDir` defaults to `/var/lib/tg-agent-permissions` and `outboxDir` to `/var/lib/tg-agent-outbox`, both outside the StateDirectory. Inside it, systemd hands the tree to the service's dynamic user and mounts it id-mapped: the service could write its own permissions, the outbox refused its downloads with EACCES, and no person could open one. Existing permission files have to be moved to the new directory by hand

### Fixed

- File names in any alphabet. A name was cut down to ASCII letters, so `Отчет.pdf` arrived as `-----.pdf`, both in the outbox and on the other side of an upload. Path separators and control characters are still replaced
- `media` downloads. The service read `--media-dir` and then never used it, so every download was refused with "this service has no outbox", whatever the unit and the permissions said

### Removed

- The outbox's own lifetime. The module's daily sweep, set by `keepMediaDays`, is the only thing that expires a download

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
