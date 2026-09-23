"""What the first live run found: the three faults behind one empty answer.

Each of these passed the suite and failed against a real account, which is the shape of
defect a stand-in cannot catch on its own. They are pinned here so the stand-in catches
them from now on.
"""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / "1271479041.conf").write_text("allow: all\n")
    return permissions.Store(tmp_path)


@pytest.fixture
def fake():
    return FakeClient(
        messages={1271479041: [Message(id=1, text="a note", sender_id=1, out=True)]},
        dialogs=[Dialog(id=1271479041, name="saved", unread_count=0)],
    )


@pytest.fixture
def ask(store, fake, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    return call


# Telethon resolves a string as a username or a phone number, never as an identifier, so
# a request carrying "1271479041" reaches the account as a name nobody has
def test_an_identifier_reaches_the_account_as_a_number(ask, fake):
    answer = ask({"action": "read", "chat": "1271479041", "limit": 5})
    assert answer["ok"] is True, answer
    assert fake.called("get_messages")[0]["entity"] == 1271479041


def test_a_negative_identifier_survives_the_conversion(ask, fake, store):
    # Groups are negative and channels carry a -100 prefix, so the sign is part of the
    # number rather than something to strip on the way through
    (store.root / "chats" / "-1001234567890.conf").write_text("allow: read\n")
    answer = ask({"action": "read", "chat": "-1001234567890"})
    assert answer["ok"] is True, answer
    entities = [call["entity"] for call in fake.called("get_messages")]
    assert -1001234567890 in entities


# An unexpected failure has to come back as an answer. Letting it close the connection
# leaves the caller with an empty read and no idea which of its assumptions was wrong
def test_an_unexpected_failure_is_answered_rather_than_dropped(ask, store, monkeypatch):
    async def explode(*args, **kwargs):
        raise ValueError('Cannot find any entity corresponding to "1271479041"')

    monkeypatch.setattr(client_module.Client, "history", explode)
    answer = ask({"action": "read", "chat": 1271479041})
    assert answer["ok"] is False
    assert "Cannot find any entity" in answer["error"]


def test_the_answer_to_a_failure_names_where_it_came_from(ask, monkeypatch):
    async def explode(*args, **kwargs):
        raise RuntimeError("the network went away")

    monkeypatch.setattr(client_module.Client, "history", explode)
    answer = ask({"action": "read", "chat": 1271479041})
    assert "RuntimeError" in answer["error"]


# The session caches each entity's access hash, and an identifier cannot be resolved
# without it. A fresh session has an empty cache, so the first thing to do is fill it
def test_starting_fills_the_entity_cache(fake, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    account = client_module.Client(fake)
    asyncio.run(account.start())
    assert fake.called("get_dialogs"), "the cache was left empty, so no chat resolves"
