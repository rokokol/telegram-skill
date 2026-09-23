"""Searching asks the server, so finding one message never reads a whole history."""

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
    (tmp_path / "chats" / "777.conf").write_text("allow: read\n")
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(messages={777: [Message(id=1, text="a note", sender_id=1)]})
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def test_a_search_term_reaches_the_server(ask):
    answer = ask({"action": "read", "chat": 777, "search": "ROKOKOL.md"})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")[0]["search"] == "ROKOKOL.md"


def test_a_read_without_a_term_asks_for_no_search(ask):
    ask({"action": "read", "chat": 777})
    assert "search" not in ask.fake.called("get_messages")[0]


def test_a_search_still_marks_nothing_read(ask):
    ask({"action": "read", "chat": 777, "search": "anything"})
    assert not ask.fake.called("send_read_acknowledge")
