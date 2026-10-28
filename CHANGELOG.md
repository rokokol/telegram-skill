# Changelog

Kept in the shape of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), dated rather than numbered, and with no `Unreleased` section — a skill is read at whatever revision you have checked out, so whatever is on the default branch is what everyone already has, and a section for work that has landed but not shipped would never close. The rule lives in the [versioning](https://github.com/rokokol/versioning-skill) skill, which owns what has no version

## 2026-10-28

### Added

- `read` includes reaction counts and the account's choices, and `read --ids` fetches specific messages. `react --id ID --reactions EMOJI...` replaces the account's emoji reactions; an empty `--reactions` removes them. Reacting requires `write`, available for chats and folders without granting message sends
- `mark-read --folder ID` marks history read in every current member, pinned chats included, under a separate folder `mark-read` grant. The answer lists the marked chats and reports membership drift; the grant also permits marking an individual member read and prevents edits that widen the folder's reach

### Changed

- `SKILL.md` names what a read message does and does not carry: no date, so `id` orders a chat and a year comes out of the text, and `--search` is a substring match rather than a stem, requiring separate searches merged by `id` for inflected forms

## 2026-10-24

### Added

- `dialogs`: every chat with its name and unread count, archived chats included, marking nothing read. Only `chats/default.conf` grants it, and `all` does not, because the list spans the account rather than one chat
- `chats/default.conf` and `folders/default.conf` apply to every target of their kind without a file of its own, the account-wide form of mail's `allow:` line. A target's own file replaces the default whole, the survey lists it as `default`, and every answer it decides says so
- `--qr` for `--login`: sign in by scanning a QR code with a device that is already signed in, so no code goes through Telegram or SMS. A token that expires is replaced and shown again
- The login says where Telegram sent the code and how many digits it holds: the app, SMS, a call, and the other `auth.SentCodeType` constructors
- A flood wait names the seconds Telegram asks to wait, instead of leaving the prompt open with no code

### Changed

- `tg-agent-login` passes its arguments through, so `sudo tg-agent-login --qr` reaches the service

### Fixed

- A dropped link to Telegram is picked up again with a pause that doubles up to a minute, and an outage past the budget ends the process with a temporary-failure code, so `Restart=on-failure` brings the service back instead of it staying alive with no link. Telethon's own reconnect is off: it retried a throttling answer with no pause at all, which is what made the link answer a throttling page, and then stopped reconnecting for good
- A request made while the link is down answers `Telegram is unreachable: the service is reconnecting` rather than a bare connection error, and a subscription that loses the link ends with that reason instead of closing with no answer
- `tg-agent-login` refused with "Unit tg-agent-login.service was already loaded" when an earlier attempt still held the unit, and a closed terminal leaves that attempt running. It now stops and clears the old unit first
- A client that leaves before reading its answer no longer reaches the journal as `Unhandled exception in client_connected_cb`: the failed write raised `ConnectionResetError` out of the session, and the session now ends quietly. The rejection of an oversized line owed to a peer that is already gone ends the same way
- The refusal of an oversized line is decided by the reader alone. A `ValueError` raised anywhere else in a session, in the handler included, used to be answered as `request too large`, hiding the fault that caused it

## 2026-10-22

### Added

- `services.tg-agent.skill`, read-only: the skill's own files at the service's revision, for a configuration that installs skills declaratively
- `subscribe`: the service passes on each new message of a chat as Telegram pushes it, under the chat's `read` grant, decided again for every message. A subscriber that stops reading is cut off with a reason rather than held in memory
- `subscribe` takes a `topic`, and passes on only that forum topic
- `tg-watch.py` follows a chat or a topic through `subscribe` and shows a message as it lands. A stream the service closed is opened again at once; `--interval` is now only the wait after the service could not be reached or refused, and defaults to 5 seconds. A `--once` run still reads once

- `whoami` describes the account: identifier, name, username and Premium. It needs no grant
- `chat-info` describes a chat under its `read` grant. For a person: name, username, Premium, phone when they share it, last seen and bio. For a group or a channel: its kind, title, username, size and description

- `read` shows a rich message as Markdown in its `text`, built from the message's blocks: headings, tables with their alignment, lists with checkboxes, code, quotes and formulas. A message the server sent only in part is fetched whole first. Before, such a message read as empty
- `tg-watch.py --state FILE` keeps the last message seen between runs, so a hook can start the watcher again and get only what is new
- `tg-watch.py --exec CMD` runs a shell command for each new message, with the message and its chat as one JSON line on standard input
- `tg.py` remembers what it sent with `send` and `reply`, in `$XDG_STATE_HOME/tg-agent/sent` or `$TG_SENT_LOG`, and `tg-watch.py` leaves those messages out. What the user sends from another device still shows

- `--parse-mode` for `send`, `reply` and `edit`: `md`, `html`, `none` or `rich`. `rich` sends Telegram's rich Markdown, which the server parses, with headings, tables, lists and formulas. A file's caption cannot be `rich`, and such a request is refused
- A custom emoji in the text is refused when the account has no Telegram Premium, where the server would drop it from the message

### Changed

- The markup is named to Telethon on every write, Markdown by default. Before, the service left it to Telethon's client default, which was also Markdown

### Fixed

- `tg-watch.py` ended with "Connection refused" whenever the service restarted, as on every system rebuild. It now waits the restart out and goes on from the last message it saw
- A refused `topics` named `topics` in its remedy, a word the permission file rejects. The remedy now names `read`, the grant the action takes

## 2026-10-18

### Added

- `saved` names the account's saved messages wherever a request takes a chat: `--chat saved`, and `--to saved` for `forward`. The service puts the account's own identifier in its place, so the grant is still the permission file of that number

## 2026-09-30

### Added

- A NixOS VM test of the module, `checks.<system>.vm`, run in CI. It boots the unit with a probe in place of the service and holds four properties: a download is readable by a person, the session is not, the service cannot write its permissions, and only the socket user reaches the socket
- An assertion that `permissionsDir` and `outboxDir` lie outside the service's StateDirectory
- Sending a file with `send` or `reply` and `tg.py --file`, under the same grant as a message. The client reads the file and puts its bytes into the request; the service never opens a path. A file may be up to 50 MiB, and a larger one is refused with an answer rather than a closed connection

### Changed

- `permissionsDir` defaults to `/var/lib/tg-agent-permissions` and `outboxDir` to `/var/lib/tg-agent-outbox`, both outside the StateDirectory. Inside it, systemd hands the tree to the service's dynamic user and mounts it id-mapped: the service could write its own permissions, the outbox refused its downloads with EACCES, and no person could open one. Existing permission files have to be moved to the new directory by hand

### Fixed

- A download is named after the document it holds, where it was named `ID.bin` every time; an attachment with no name of its own gets its message and extension. The service read a name from `message.file_name`, which Telethon does not have
- A download of an identifier the chat does not hold, or of a message with nothing attached, answered success with the path `None`. The answer now lists what was `written`, and names `missing` and `no_file` only when either holds something. `read` names a document's file
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
