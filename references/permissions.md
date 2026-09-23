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

## Why `all` is reported wherever it is used

`allow: all` grants every word in the target's table. It suits a target with no other party in it, such as the account's own saved messages, and it is a different decision from naming an action: the holder chose one word and received a table that includes deleting for everyone and forwarding out

So every answer permitted by `all` says so, and a survey of what is held lists it expanded. Neither is a warning about the grant; both keep its reach visible

## Folders grant reading, never writing

A folder's membership changes from any device, without the service being asked. That is what makes a folder worth granting reading on, and what makes writing through one impossible to offer: a message sent to a chat that joined yesterday cannot be recalled

The same property forces one refusal. A folder whose `allow` holds `read`, `digest` or `media` refuses `folder-edit`, `folder-create` and `folder-delete`, even when its own file lists them. Without that refusal the agent adds a chat to a folder it may read, and reads a chat nobody granted it. A folder that grants only renaming or reordering has no such reach and stays editable

## Turning a refusal into a permission

A refused request names the line that would lift it. Take that line to the user, with what was being attempted and why. They apply it; the service reads the files fresh on every request, so the next attempt sees the change with nothing restarted
