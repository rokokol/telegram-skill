"""The agent can ask what it holds, so it never probes for permissions it lacks."""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient
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
def ask(store, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(dialogs=[Dialog(id=777, name="a chat", unread_count=2)])
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def test_an_empty_store_reports_nothing_held(ask):
    answer = ask({"action": "permissions"})
    assert answer["ok"] is True
    assert answer["result"] == {"chats": {}, "folders": {}}


def test_each_target_is_listed_with_the_words_it_grants(ask, store):
    (store.root / "chats" / "777.conf").write_text("allow: read, send\n")
    (store.root / "folders" / "5.conf").write_text("allow: digest\n")
    answer = ask({"action": "permissions"})
    assert answer["result"]["chats"]["777"] == ["read", "send"]
    assert answer["result"]["folders"]["5"] == ["digest"]


def test_a_wildcard_is_listed_expanded_so_its_reach_is_visible(ask, store):
    (store.root / "chats" / "777.conf").write_text("allow: all\n")
    held = ask({"action": "permissions"})["result"]["chats"]["777"]
    assert "delete-for-all" in held
    assert "forward" in held


def test_a_broken_file_is_named_rather_than_hiding_the_rest(ask, store):
    (store.root / "chats" / "777.conf").write_text("allow: read\n")
    (store.root / "chats" / "778.conf").write_text("allow: shout\n")
    answer = ask({"action": "permissions"})
    assert answer["result"]["chats"]["777"] == ["read"]
    assert "shout" in answer["result"]["chats"]["778"][0]


def test_surveying_permissions_touches_no_account(ask):
    ask({"action": "permissions"})
    assert ask.fake.calls == []
