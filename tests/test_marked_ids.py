"""A folder stores raw peer identifiers; a chat is addressed by the marked one."""

import pytest

from tg_agentd import folders


class Peer:
    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


# The three peer kinds carry different fields and mark differently. Getting this wrong
# makes a channel in a folder address a user that does not exist, which is what the first
# live run did
def test_a_user_keeps_its_number():
    assert folders._identifier(Peer(user_id=1271479041)) == 1271479041


def test_a_basic_group_is_negative():
    assert folders._identifier(Peer(chat_id=123456789)) == -123456789


def test_a_channel_carries_the_hundred_prefix():
    # 2303572307 in a folder is -1002303572307 everywhere else
    assert folders._identifier(Peer(channel_id=2303572307)) == -1002303572307


def test_an_identifier_already_marked_is_left_alone():
    assert folders._identifier(-1002303572307) == -1002303572307
    assert folders._identifier(777) == 777


@pytest.mark.parametrize(
    "peer,expected",
    [
        (Peer(user_id=805109040), 805109040),
        (Peer(channel_id=2339852045), -1002339852045),
        (Peer(channel_id=3730561121), -1003730561121),
    ],
)
def test_the_members_of_a_folder_are_addressable(peer, expected):
    folder = Peer(include_peers=[peer], pinned_peers=[])
    assert folders.members_of(folder) == [expected]


def test_a_folder_mixes_kinds_without_confusing_them(monkeypatch):
    folder = Peer(
        include_peers=[Peer(user_id=777), Peer(channel_id=2303572307)],
        pinned_peers=[Peer(chat_id=42)],
    )
    assert folders.members_of(folder) == [-1002303572307, -42, 777]
