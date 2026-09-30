"""The directory downloaded files land in, and the rules that keep them inside it.

Two names reach this module from outside and neither is trusted. The agent chooses the
subdirectory, and whoever sent the attachment chose its file name. Both are reduced to a
single path component before they become a path.

Expiry is not here: the module's sweep timer deletes what has outlived keepMediaDays, so
the lifetime has one owner and it runs even while nothing is downloaded.
"""

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
    """Where downloads land."""

    def __init__(self, root):
        self.root = Path(root)

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
