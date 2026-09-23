"""A folder grants reading, and a folder that grants reading cannot be edited."""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient, FakeFolder, Message
from tg_agentd import client as client_module
from tg_agentd import folders, handler, permissions


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
    fake = FakeClient(
        messages={
            777: [Message(id=1, text="unread one", sender_id=42)],
            888: [Message(id=2, text="unread two", sender_id=43)],
        },
        dialogs=[
            Dialog(id=777, name="study one", unread_count=1),
            Dialog(id=888, name="study two", unread_count=2),
            Dialog(id=999, name="private", unread_count=5),
        ],
        folders=[FakeFolder(id=5, title="study", include_peers=[777, 888])],
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def grant_folder(store, folder, words, extra=""):
    (store.root / "folders" / f"{folder}.conf").write_text(f"allow: {words}\n{extra}")


def test_listing_folders_needs_its_own_grant(ask, store):
    answer = ask({"action": "folder-read"})
    assert answer["ok"] is False


def test_a_granted_folder_lists_the_chats_it_holds_now(ask, store):
    grant_folder(store, 5, "folder-read")
    answer = ask({"action": "folder-read", "folder": 5})
    assert answer["ok"] is True
    assert answer["result"]["members"] == [777, 888]


def test_a_digest_counts_only_the_chats_inside_the_folder(ask, store):
    grant_folder(store, 5, "digest")
    answer = ask({"action": "digest", "folder": 5})
    assert answer["ok"] is True
    counted = {entry["chat"]: entry["unread"] for entry in answer["result"]["chats"]}
    assert counted == {777: 1, 888: 2}
    # The private chat is outside the folder, so the folder grant never reaches it
    assert 999 not in counted


def test_a_digest_marks_nothing_read(ask, store):
    grant_folder(store, 5, "digest")
    ask({"action": "digest", "folder": 5})
    assert not ask.fake.called("send_read_acknowledge")


# The invariant: a folder the agent may read is a folder the agent may not fill
def test_a_folder_that_grants_reading_refuses_to_be_edited(ask, store):
    grant_folder(store, 5, "read, folder-edit")
    answer = ask({"action": "folder-edit", "folder": 5, "add": [999]})
    assert answer["ok"] is False
    assert "grants content" in answer["error"]
    assert not ask.fake.called("raw_update_filter")


def test_a_folder_that_grants_no_reading_may_be_edited(ask, store):
    grant_folder(store, 5, "folder-read, folder-edit")
    answer = ask({"action": "folder-edit", "folder": 5, "add": [999]})
    assert answer["ok"] is True
    assert ask.fake.called("raw_update_filter")


def test_membership_that_drifted_since_the_grant_is_reported(ask, store):
    # The user adds a chat to the folder from a phone. The grant widens without being
    # edited, so every answer through that folder says the membership moved
    members = folders.fingerprint([777, 888])
    grant_folder(store, 5, "digest", extra=f"members: {members}\n")
    assert "drift" not in ask({"action": "digest", "folder": 5})

    grant_folder(store, 5, "digest", extra=f"members: {folders.fingerprint([777])}\n")
    answer = ask({"action": "digest", "folder": 5})
    assert answer["ok"] is True
    assert "membership changed" in answer["warning"]


def test_a_folder_with_no_recorded_membership_is_not_reported_as_drifted(ask, store):
    grant_folder(store, 5, "digest")
    assert "warning" not in ask({"action": "digest", "folder": 5})
