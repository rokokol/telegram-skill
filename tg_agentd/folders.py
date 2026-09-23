"""Folders as a source of permission, and the fingerprint that keeps them honest.

A folder's membership is the user's to change, from any device, without telling this
service. That is what makes a folder convenient to grant reading on and impossible to
grant writing on. The fingerprint recorded in a permission file is how a grant says which
membership it was given for, so an answer can report that the membership has moved since.
"""

import hashlib


def fingerprint(members):
    """A short digest of a membership, stable under ordering."""
    joined = ",".join(str(member) for member in sorted(members))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def title_of(folder):
    """A folder's title as text.

    Telegram wraps it in a container carrying formatting entities, so the object's own
    string form is the container rather than the name a person reads.
    """
    title = getattr(folder, "title", "")
    return str(getattr(title, "text", title) or "")


def members_of(folder):
    """The chat identifiers a folder holds, as plain integers.

    Telegram keeps a folder's pinned chats in a field of their own, so reading only the
    included ones leaves out every chat the user pinned there — which is usually the ones
    they care about most.

    The identifiers arrive as input peers. The number is the part a permission file can
    name, so it is the part this service works in.
    """
    members = []
    for field in ("pinned_peers", "include_peers"):
        for peer in getattr(folder, field, []) or []:
            members.append(_identifier(peer))
    return sorted({m for m in members if m is not None})


# A channel's marked identifier is its own with this added and the sign flipped, which is
# what the -100 prefix means when written out
CHANNEL_MARK = 1000000000000


def _identifier(peer):
    """A peer as the number the rest of Telegram addresses it by.

    A folder stores the raw identifier of each kind, while a dialog, a permission file and
    every request use the marked one. Reading a folder without marking makes a channel
    address a user that does not exist.
    """
    if isinstance(peer, int):
        return peer
    value = getattr(peer, "user_id", None)
    if value is not None:
        return value
    value = getattr(peer, "chat_id", None)
    if value is not None:
        return -value
    value = getattr(peer, "channel_id", None)
    if value is not None:
        return -(CHANNEL_MARK + value)
    return None


def drift(recorded, current):
    """A sentence naming a membership change, or None when nothing moved.

    A permission file with no recorded fingerprint is not drifted: it was written without
    one, and inventing a complaint for it would train the reader to ignore the warning.
    """
    if not recorded:
        return None
    if recorded == fingerprint(current):
        return None
    return (
        f"membership changed since the grant was written: holds {len(current)} chats, "
        f"fingerprint {fingerprint(current)} against the recorded {recorded}"
    )
