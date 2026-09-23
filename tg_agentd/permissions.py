"""Read the permission files and decide whether an action is allowed.

The files live in a directory the agent cannot write. The agent proposes an edit and the
user applies it; nothing here writes. A missing file is a refusal, not an error, so a
chat nobody granted anything on behaves the same as one that does not exist.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import verbs

# The identifier reaches this module from the agent, and a file path is built from it.
# Telegram identifiers are integers: users positive, groups negative, channels prefixed
# with -100. Anything else is refused before it can become a path
IDENTIFIER = re.compile(r"\A-?[0-9]{1,20}\Z")

TRUE_WORDS = frozenset({"yes", "true", "on", "1"})


class BadIdentifier(ValueError):
    """The chat or folder identifier is not a plain Telegram identifier."""


def as_identifier(value):
    """Validate an identifier and return it as a number.

    The number matters as much as the validation. Telethon resolves a string as a
    username or a phone number, never as an identifier, so a chat passed as text reaches
    the account as a name nobody has.
    """
    name = str(value)
    if not IDENTIFIER.match(name):
        raise BadIdentifier(f"{name!r} is not a Telegram identifier")
    return int(name)


class PermissionFileError(ValueError):
    """The permission file holds a word no action table defines."""


class UnknownAction(ValueError):
    """The caller asked about an action that no table defines."""


@dataclass(frozen=True)
class Decision:
    """Whether an action is allowed, and what decided it.

    `by` holds the granted word that permitted the action. It differs from the action
    itself when a wildcard permitted it, and every report says which, because a wildcard
    was one word standing for a whole table.
    """

    allowed: bool
    by: str | None
    reason: str


class Store:
    """The permission files, read fresh on every question.

    Nothing is cached. The user edits these files between requests, and a cached grant
    would outlive a permission they withdrew.
    """

    def __init__(self, root):
        self.root = Path(root)

    def chat_may(self, chat_id, action):
        """Decide whether this action is allowed on this chat."""
        if action not in verbs.CHAT_ACTIONS:
            raise UnknownAction(f"no chat action is called {action!r}")
        return self._decide("chats", chat_id, action, verbs.CHAT_ACTIONS)

    def folder_may(self, folder_id, action):
        """Decide whether this action is allowed on this folder.

        A folder that grants reading of its contents refuses every action that could add
        a chat to it. Without that refusal the agent widens its own reach: it puts a chat
        into a folder it may read, and reads a chat nobody granted it.
        """
        if action not in verbs.FOLDER_ACTIONS:
            raise UnknownAction(f"no folder action is called {action!r}")
        granted = self._granted_words("folders", folder_id, verbs.FOLDER_ACTIONS)
        if granted is None:
            return self._no_file("folders", folder_id)
        if verbs.widens_reach(action) and verbs.grants_content(granted):
            return Decision(
                False,
                None,
                f"folder {folder_id} grants content, so it cannot be edited: "
                f"holds {', '.join(sorted(granted))}",
            )
        return self._weigh(granted, action, verbs.FOLDER_ACTIONS, "folder", folder_id)

    def survey(self):
        """Every target that holds a grant, with the actions each one expands to.

        A wildcard is listed expanded rather than as one word, so that reading this
        answer shows the reach it actually carries. A file that cannot be parsed is
        reported in place of its actions, because hiding it would make a broken grant
        look like a missing one.
        """
        return {
            "chats": self._survey_kind("chats", verbs.CHAT_ACTIONS),
            "folders": self._survey_kind("folders", verbs.FOLDER_ACTIONS),
        }

    def _survey_kind(self, kind, table):
        held = {}
        directory = self.root / kind
        if not directory.is_dir():
            return held
        for path in sorted(directory.glob("*.conf")):
            name = path.stem
            try:
                words = self._granted_words(kind, name, table) or set()
            except (PermissionFileError, BadIdentifier) as broken:
                held[name] = [str(broken)]
                continue
            actions = set()
            for word in words:
                actions.update(verbs.expand(word, table))
            held[name] = sorted(actions)
        return held

    def chat_flag(self, chat_id, name):
        """Read a boolean setting from a chat's permission file, false when absent."""
        fields = self._fields("chats", chat_id)
        if fields is None:
            return False
        return fields.get(name, "").strip().lower() in TRUE_WORDS

    def folder_field(self, folder_id, name):
        """Read a plain setting from a folder's permission file, empty when absent."""
        fields = self._fields("folders", folder_id)
        if fields is None:
            return ""
        return fields.get(name, "").strip()

    def _decide(self, kind, identifier, action, table):
        granted = self._granted_words(kind, identifier, table)
        if granted is None:
            return self._no_file(kind, identifier)
        return self._weigh(granted, action, table, kind.rstrip("s"), identifier)

    def _weigh(self, granted, action, table, noun, identifier):
        by = verbs.granted(action, granted, table)
        if by is None:
            held = ", ".join(sorted(granted)) or "nothing"
            return Decision(
                False, None, f"{noun} {identifier} does not allow {action}: holds {held}"
            )
        return Decision(True, by, f"{noun} {identifier} allows {action}")

    def _no_file(self, kind, identifier):
        return Decision(
            False, None, f"no permission file for {kind.rstrip('s')} {identifier}"
        )

    def _granted_words(self, kind, identifier, table):
        """The words the file grants, or None when there is no file."""
        fields = self._fields(kind, identifier)
        if fields is None:
            return None
        words = [w.strip() for w in fields.get("allow", "").split(",") if w.strip()]
        unknown = verbs.unknown_words(words, table)
        if unknown:
            raise PermissionFileError(
                f"{self._path(kind, identifier)} grants "
                f"{', '.join(unknown)}, which is no {kind.rstrip('s')} action"
            )
        return set(words)

    def _fields(self, kind, identifier):
        """Parse the file into fields, or None when there is no file."""
        path = self._path(kind, identifier)
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None
        fields = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition(":")
            if not separator:
                raise PermissionFileError(f"{path}: line is not key: value: {line!r}")
            fields[key.strip()] = value.strip()
        return fields

    def _path(self, kind, identifier):
        return self.root / kind / f"{as_identifier(identifier)}.conf"
