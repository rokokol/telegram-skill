"""The client is invisible unless told otherwise, and never guesses a destructive default.

Each test here pins a property the plan calls checkable rather than promised: presence is
switched off by an explicit call, reading never announces itself, and the flag that
decides who loses a message is always passed.
"""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient, Message
from tg_agentd import client as client_module


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def fake():
    return FakeClient(
        messages={777: [Message(id=1, text="hello", sender_id=42)]},
        dialogs=[Dialog(id=777, name="a chat", unread_count=3)],
    )


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    return client_module.Client(fake)


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


def test_starting_announces_absence_rather_than_staying_quiet(client, fake):
    # The server reflects the account as online on any high-level request, so silence is
    # not invisibility: the offline status has to be sent
    run(client.start())
    statuses = fake.called("raw")
    assert statuses, "no raw request was sent, so presence was left to the server"
    assert statuses[-1]["request"] == "_FakeStatusRequest"
    assert statuses[-1]["offline"] is True


def test_reading_history_never_marks_it_read(client, fake):
    run(client.start())
    run(client.history(777, limit=10))
    assert fake.called("get_messages"), "the history was not read at all"
    assert not fake.called("send_read_acknowledge")


def test_listing_dialogs_never_marks_anything_read(client, fake):
    run(client.start())
    run(client.dialogs())
    assert not fake.called("send_read_acknowledge")


def test_marking_read_happens_only_when_asked(client, fake):
    run(client.start())
    run(client.mark_read(777))
    assert fake.called("send_read_acknowledge") == [{"entity": 777, "message": None}]


@pytest.mark.parametrize("for_all", [True, False])
def test_deleting_always_passes_the_flag_that_decides_who_loses_the_message(
    client, fake, for_all
):
    # Telethon defaults revoke to True. A path that omits it deletes for everyone by
    # accident, so the service states it every time
    run(client.start())
    run(client.delete(777, [1, 2], for_all=for_all))
    assert fake.called("delete_messages") == [
        {"entity": 777, "message_ids": [1, 2], "revoke": for_all}
    ]


def test_sending_never_announces_typing(client, fake):
    run(client.start())
    run(client.send(777, "text"))
    assert not any(name == "typing" for name, _ in fake.calls)


def test_a_reply_carries_the_message_it_answers(client, fake):
    run(client.start())
    run(client.send(777, "text", reply_to=5))
    assert fake.called("send_message") == [
        {"entity": 777, "message": "text", "reply_to": 5}
    ]
