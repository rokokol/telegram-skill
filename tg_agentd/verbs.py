"""The actions a permission can grant, and what each one means.

This module is the single source of truth for the vocabulary. The help text prints it.
The permission parser validates against it. The dispatcher looks each request up here.
A word outside these tables is a refusal, never a silent pass.
"""

# What a chat's permission file can grant. The narrow words are separate grants, not
# consequences of a broad one: "read" never implies "media", and "send" never implies
# "forward" or "mark-read"
CHAT_ACTIONS = {
    "read": "read message history",
    "media": "download attached files into the outbox",
    "send": "send a new message",
    "reply": "reply to a message",
    "edit": "edit a message the account itself sent",
    "delete": "delete messages for this account alone",
    "delete-for-all": "delete messages for every participant, the account's own included",
    "forward": "forward messages out of this chat, which also needs send on the target",
    "mark-read": "mark history as read, which the other side can see",
}

# What a folder's permission file can grant. A folder grants no write action on its chats.
# Its membership changes without the agent asking. A message sent to a chat that joined
# the folder yesterday cannot be taken back
FOLDER_CONTENT_ACTIONS = {
    "read": "read history of every chat currently in this folder",
    "digest": "summarise unread messages across this folder",
    "media": "download attached files from this folder into the outbox",
}

# Managing folders. These words cannot change which chats the agent reaches
FOLDER_SAFE_ACTIONS = {
    "folder-read": "list folders and their membership",
    "folder-order": "reorder the folder tabs",
    "folder-rename": "change a folder's title or emoticon",
}

# These words can change which chats the agent reaches. A folder that grants a content
# action above refuses them, even when its own file lists them. Without that refusal the
# agent widens its own reach by editing a folder
FOLDER_REACH_ACTIONS = {
    "folder-edit": "add or remove chats in a folder",
    "folder-create": "create a folder",
    "folder-delete": "delete a folder",
}

FOLDER_ACTIONS = FOLDER_CONTENT_ACTIONS | FOLDER_SAFE_ACTIONS | FOLDER_REACH_ACTIONS

# One word for every action on a target. It suits a chat with no other party in it, such
# as the account's own saved messages. Every caller that reports a decision says when the
# grant came from this word rather than from the action's own name, because the holder
# chose one word and received the whole table
WILDCARD = "all"


def expand(word, table):
    """Return the action names a granted word stands for, sorted."""
    return sorted(table) if word == WILDCARD else [word]


def unknown_words(words, table):
    """Return the given words that the table does not define, in order."""
    return [w for w in words if w != WILDCARD and w not in table]


def granted(action, words, table):
    """Whether these granted words permit this action.

    Returns the word that permitted it, so the caller can report a wildcard as one.
    Returns None when nothing permits it.
    """
    if action in words:
        return action
    if WILDCARD in words and action in table:
        return WILDCARD
    return None


def widens_reach(action):
    """Whether this action can change which chats the agent reaches."""
    return action in FOLDER_REACH_ACTIONS


def grants_content(words):
    """Whether these granted words let the holder read what is inside a folder."""
    if WILDCARD in words:
        return True
    return any(w in FOLDER_CONTENT_ACTIONS for w in words)
