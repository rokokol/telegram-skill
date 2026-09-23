"""The directory downloaded files land in, and the rules that keep them inside it.

Two names reach this module from outside and neither is trusted. The agent chooses the
subdirectory, and whoever sent the attachment chose its file name. Both are reduced to a
single path component before they become a path.

The directory also expires its contents. Without that, reading a chat's attachments once
leaves a copy of them on disk for good, which is the outcome the separate media
permission exists to avoid.
"""

import time
import unicodedata
from pathlib import Path

# A component may hold letters, digits and a few separators. Everything else, the path
# separator and the null byte included, is replaced
SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")

FALLBACK = "file"


class BadDestination(ValueError):
    """The requested destination does not name a place inside the outbox."""


def component(name, *, fallback=FALLBACK):
    """Reduce a name to one safe path component.

    Unicode is normalised first, so that a composed and a decomposed spelling of one name
    cannot become two directories.
    """
    text = unicodedata.normalize("NFC", str(name))
    cleaned = "".join(character if character in SAFE else "-" for character in text)
    cleaned = cleaned.strip(" .-")
    return cleaned or fallback


class Outbox:
    """Where downloads land, and how long they stay."""

    def __init__(self, root, *, keep_for):
        self.root = Path(root)
        self.keep_for = keep_for

    def place(self, group, file_name):
        """The path a download should take, inside a subdirectory of the outbox.

        The group is the agent's choice and is rejected rather than repaired: a request
        that names somewhere else is a mistake worth reporting. The file name comes from
        the other side of a conversation and is repaired, because refusing it would let a
        correspondent block a download by naming their file oddly.
        """
        safe_group = component(group, fallback="")
        if not safe_group or safe_group != str(group):
            raise BadDestination(f"{group!r} does not name a directory inside the outbox")
        directory = self.root / safe_group
        directory.mkdir(parents=True, exist_ok=True)
        return directory / component(file_name)

    def sweep(self):
        """Delete what has outlived keep_for, and return the names removed."""
        deadline = time.time() - self.keep_for
        removed = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.stat().st_mtime < deadline:
                path.unlink()
                removed.append(path.name)
        return removed
