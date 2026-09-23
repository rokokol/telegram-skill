"""Every answer says what decided it, and a refusal says how to lift it."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


@pytest.fixture
def fake():
    return FakeClient(messages={777: [Message(id=1, text="hi", sender_id=42)]})


@pytest.fixture
def ask(store, fake, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    return call


def grant(store, chat, words):
    (store.root / "chats" / f"{chat}.conf").write_text(f"allow: {words}\n")


def test_an_allowed_action_names_the_word_that_allowed_it(ask, store):
    grant(store, 777, "read")
    answer = ask({"action": "read", "chat": 777})
    assert answer["ok"] is True
    assert answer["allowed_by"] == "read"


def test_a_wide_grant_is_reported_as_wide(ask, store):
    grant(store, 777, "all")
    answer = ask({"action": "read", "chat": 777})
    assert answer["ok"] is True
    assert answer["allowed_by"] == "all"
    assert answer["wildcard"] is True


def test_a_refusal_carries_the_reason_and_the_edit_that_lifts_it(ask, store):
    grant(store, 777, "read")
    answer = ask({"action": "send", "chat": 777, "text": "hello"})
    assert answer["ok"] is False
    assert "does not allow send" in answer["error"]
    # A refusal the user cannot act on wastes the exchange, so it names the line to add
    assert "allow: " in answer["remedy"]
    assert "send" in answer["remedy"]


def test_a_refusal_performs_nothing(ask, store, fake):
    grant(store, 777, "read")
    ask({"action": "send", "chat": 777, "text": "hello"})
    assert not fake.called("send_message")


def test_an_unknown_action_is_refused_without_touching_the_account(ask, store, fake):
    grant(store, 777, "all")
    answer = ask({"action": "launch", "chat": 777})
    assert answer["ok"] is False
    assert "launch" in answer["error"]
    assert fake.calls == []


def test_a_broken_permission_file_is_an_answer_rather_than_a_crash(ask, store):
    grant(store, 777, "read, shout")
    answer = ask({"action": "read", "chat": 777})
    assert answer["ok"] is False
    assert "shout" in answer["error"]


def test_an_identifier_that_is_not_a_number_is_refused(ask, store):
    answer = ask({"action": "read", "chat": "../../etc/passwd"})
    assert answer["ok"] is False
    assert "identifier" in answer["error"]


def test_forwarding_needs_a_grant_on_both_ends(ask, store, fake):
    grant(store, 777, "forward")
    answer = ask({"action": "forward", "chat": 777, "to": 888, "ids": [1]})
    assert answer["ok"] is False
    # The source allows forwarding, but nothing allows writing into the destination
    assert "888" in answer["error"]
    assert not fake.called("forward_messages")


def test_forwarding_goes_through_when_both_ends_allow_it(ask, store, fake):
    grant(store, 777, "forward")
    grant(store, 888, "send")
    answer = ask({"action": "forward", "chat": 777, "to": 888, "ids": [1]})
    assert answer["ok"] is True
    assert fake.called("forward_messages") == [
        {"entity": 888, "messages": [1], "from_peer": 777}
    ]


def test_deleting_for_all_is_a_separate_grant(ask, store, fake):
    grant(store, 777, "delete")
    answer = ask({"action": "delete-for-all", "chat": 777, "ids": [1]})
    assert answer["ok"] is False
    assert not fake.called("delete_messages")


def test_replying_marks_read_only_where_the_file_asks(ask, store, fake):
    grant(store, 777, "reply")
    ask({"action": "reply", "chat": 777, "text": "ok", "reply_to": 1})
    assert not fake.called("send_read_acknowledge")

    (store.root / "chats" / "778.conf").write_text(
        "allow: reply\nmark-read-on-reply: yes\n"
    )
    ask({"action": "reply", "chat": 778, "text": "ok", "reply_to": 1})
    assert fake.called("send_read_acknowledge")
