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


def members_of(folder):
    """The chat identifiers a folder holds, as plain integers.

    Telegram stores these as input peers. The identifier is the part a permission file
    can name, so it is the part this service works in.
    """
    members = []
    for peer in getattr(folder, "include_peers", []) or []:
        members.append(_identifier(peer))
    return sorted(m for m in members if m is not None)


def _identifier(peer):
    if isinstance(peer, int):
        return peer
    for attribute in ("user_id", "chat_id", "channel_id"):
        value = getattr(peer, attribute, None)
        if value is not None:
            return value
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
