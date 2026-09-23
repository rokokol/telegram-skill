"""A folder grant reaches the chats inside it, and says so in the answer."""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient, FakeFolder, Message
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
    fake = FakeClient(
        messages={777: [Message(id=1, text="inside the folder", sender_id=42)]},
        dialogs=[Dialog(id=777, name="a chat", unread_count=1)],
        folders=[FakeFolder(id=5, title="study", include_peers=[777, 888])],
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def grant_folder(store, folder, words):
    (store.root / "folders" / f"{folder}.conf").write_text(f"allow: {words}\n")


# The point of granting read on a folder is reaching what is inside it. Without this the
# grant can be written and never used
def test_a_chat_inside_a_granted_folder_can_be_read(ask, store):
    grant_folder(store, 5, "read")
    answer = ask({"action": "read", "chat": 777})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")


def test_the_answer_names_the_folder_that_allowed_it(ask, store):
    grant_folder(store, 5, "read")
    answer = ask({"action": "read", "chat": 777})
    assert answer["through_folder"]["id"] == 5
    # How many chats the folder holds right now is part of knowing what the grant means
    assert answer["through_folder"]["chats"] == 2


def test_a_chat_outside_every_granted_folder_is_still_refused(ask, store):
    grant_folder(store, 5, "read")
    answer = ask({"action": "read", "chat": 999})
    assert answer["ok"] is False


def test_a_folder_never_lends_a_write_action(ask, store):
    grant_folder(store, 5, "all")
    answer = ask({"action": "send", "chat": 777, "text": "no"})
    assert answer["ok"] is False
    assert not ask.fake.called("send_message")


def test_a_chat_of_its_own_is_preferred_over_the_folder(ask, store):
    grant_folder(store, 5, "read")
    (store.root / "chats" / "777.conf").write_text("allow: read\n")
    answer = ask({"action": "read", "chat": 777})
    assert answer["allowed_by"] == "read"
    assert "through_folder" not in answer


# A folder can hold a chat with no dialog — a contact nobody ever wrote to. That is
# ordinary rather than a loss, so the counts are reported as facts and not as a warning:
# a caveat that fires on almost every answer stops being read
def test_a_digest_reports_what_it_covered_against_what_the_folder_holds(ask, store):
    grant_folder(store, 5, "digest")
    answer = ask({"action": "digest", "folder": 5})
    assert answer["result"]["counted"] == 1
    assert answer["result"]["members"] == 2
    assert "warning" not in answer


def test_a_folder_title_is_text_rather_than_its_container(ask, store):
    class Titled:
        text = "study"

    ask.fake.folders[0].title = Titled()
    grant_folder(store, 5, "folder-read")
    answer = ask({"action": "folder-read", "folder": 5})
    assert answer["result"]["title"] == "study"


def test_the_folder_listing_leaves_out_the_one_with_no_identifier(ask):
    # Premium accounts carry a default "all chats" filter, which has no id to grant on
    ask.fake.folders.append(FakeFolder(id=None, title="", include_peers=[]))
    listed = ask({"action": "folder-list"})["result"]["folders"]
    assert all(entry["id"] is not None for entry in listed)
