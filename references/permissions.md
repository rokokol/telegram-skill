# The permission files

One file per target, in a directory the service reads and the agent cannot write. The agent proposes a line; the user applies it. That asymmetry is the whole design: a permission the agent can edit is not a permission

```
<permissions>/chats/-1001234567890.conf
<permissions>/folders/5.conf
```

The name is the Telegram identifier and nothing else. Users are positive, ordinary groups negative, channels carry a `-100` prefix. A name that is not a plain number is refused before it becomes a path

## The format

Plain `key: value` lines. A `#` opens a comment, blank lines are ignored

```
allow: read, reply
mark-read-on-reply: yes
members: 6f3a9c21b4d8
```

- **`allow`** is the list of granted words, separated by commas. `tg-agentd --help` prints every word with what it permits; a word outside that vocabulary is an error naming the file, never a silent pass
- **`mark-read-on-reply`** makes a reply mark the chat read. A reply with no preceding read receipt is a sequence no person produces, so leaving this off is visible to the other side
- **`members`**, on a folder, records the membership the grant was written for. An answer through a folder whose membership has moved since carries a warning. A file without it is not reported as drifted

## A grant for every target

`chats/default.conf` applies to every chat without a file of its own, and `folders/default.conf` to every folder without one. A target's own file replaces the default whole: the two are never merged, so a chat or folder outside a wide grant needs its own file naming what stays allowed

```text
chats/default.conf     allow: read, media, dialogs
folders/default.conf   allow: digest
```

This is the account-wide form of mail's `allow:` line with no address. It also covers targets created later, which a folder membership does not. The survey lists it under the name `default`, and every answer it decides says so, so a wide grant cannot pass for a per-target one

`dialogs` is an account action rather than a chat action: it lists every chat with its name and unread count, archived chats included, and marks nothing read. Only the default chat file may grant it; a per-chat file that names it is an error, and `all` does not grant it, because the list spans the account rather than one chat

A `members` fingerprint is never taken from a default, since membership differs for every folder. A default folder file that grants content still refuses folder edits, because the refusal follows the grant rather than the file that holds it

## Why `all` is reported wherever it is used

`allow: all` grants every word in the target's table. It suits a target with no other party in it, such as the account's own saved messages, and it is a different decision from naming an action: the holder chose one word and received a table that includes deleting for everyone and forwarding out

So every answer permitted by `all` says so, and a survey of what is held lists it expanded. Neither is a warning about the grant; both keep its reach visible

## Reactions and folder content access

`read` includes reaction counts and the account's chosen reactions on each returned message. Use `read --ids` to fetch specific messages. `write` permits `react` to replace or remove the account's emoji reactions; it does not grant `send`, `reply` or reading. Paid reactions are read-only through this interface

A folder's `read` or `write` grants the corresponding access to its current member chats, pinned chats included. Target a member chat when reacting; the answer names the folder that allowed it. Membership can change from any device, so this also covers chats added later. Folders do not grant sending messages

`mark-read` is a separate folder grant: it marks history read in every current member, pinned chats included, and the other side can see the read receipt. `read` and `digest` never imply it. The answer lists the affected chats under `marked`

The same property forces one refusal. A folder whose `allow` holds `read`, `write`, `digest`, `media` or `mark-read` refuses `folder-edit`, `folder-create` and `folder-delete`, even when its own file lists them. Without that refusal the agent adds a chat to a folder and reaches a chat nobody granted it. A folder that grants only renaming or reordering has no such reach and stays editable

## Turning a refusal into a permission

A refused request names the line that would lift it. Take that line to the user, with what was being attempted and why. They apply it; the service reads the files fresh on every request, so the next attempt sees the change with nothing restarted
