"""A chat is known by its name, and the grants already name the chats a search may reach.

Listing the account's dialogs is a grant of its own, so looking a chat up by name works
from the permission files instead: a chat a file names, and the members of a folder that
grants reading, are the chats whose name this account may report.
"""

import asyncio

import pytest

from tests.fake_telethon import Channel, FakeClient, FakeFolder, User
from tg_agentd import client as client_module
from tg_agentd import handler, permissions

CLUB = -1002303572307
ANNA = 777
TEAMMATE = 888


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / f"{CLUB}.conf").write_text("allow: read\n")
    (tmp_path / "chats" / f"{TEAMMATE}.conf").write_text("allow: send\n")
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store):
    fake = FakeClient(
        entities={
            CLUB: Channel(id=2303572307, title="Technical club", megagroup=True),
            ANNA: User(id=ANNA, first_name="Anna", last_name="Lee"),
            TEAMMATE: User(id=TEAMMATE, first_name="Anna", last_name="Smith"),
        }
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def found(answer):
    assert answer["ok"] is True, answer
    return answer["result"]["chats"]


def test_a_named_chat_is_found_by_part_of_its_title(ask):
    assert found(ask({"action": "find", "name": "club"})) == [
        {"chat": CLUB, "name": "Technical club"}
    ]


def test_a_person_is_found_by_their_name(ask, store):
    (store.root / "chats" / f"{ANNA}.conf").write_text("allow: read\n")
    assert found(ask({"action": "find", "name": "ann"})) == [
        {"chat": ANNA, "name": "Anna Lee"}
    ]


def test_a_name_matches_whatever_its_case(ask):
    assert [entry["chat"] for entry in found(ask({"action": "find", "name": "TECHNICAL"}))] == [
        CLUB
    ]


# A chat the agent may only write into keeps its name: the title is part of what reading
# it holds, and chat-info is refused there for the same reason
def test_a_chat_that_may_only_be_written_stays_unnamed(ask):
    assert found(ask({"action": "find", "name": "Smith"})) == []
    assert [call["entity"] for call in ask.fake.called("get_entity")] != [TEAMMATE]


# A folder that grants reading holds the names of its members, which is how a chat with
# no permission file of its own is reached at all
def test_a_chat_a_granted_folder_holds_is_found(store):
    (store.root / "folders" / "5.conf").write_text("allow: read\n")
    fake = FakeClient(
        entities={ANNA: User(id=ANNA, first_name="Anna")},
        folders=[FakeFolder(id=5, title="Work", include_peers=[ANNA])],
    )
    served = handler.Handler(store, client_module.Client(fake))
    answer = asyncio.run(served.handle({"action": "find", "name": "anna"}))
    assert found(answer) == [{"chat": ANNA, "name": "Anna"}]


def test_a_folder_that_grants_no_read_holds_no_names(store):
    (store.root / "folders" / "5.conf").write_text("allow: digest\n")
    fake = FakeClient(
        entities={ANNA: User(id=ANNA, first_name="Anna")},
        folders=[FakeFolder(id=5, title="Work", include_peers=[ANNA])],
    )
    served = handler.Handler(store, client_module.Client(fake))
    answer = asyncio.run(served.handle({"action": "find", "name": "anna"}))
    assert found(answer) == []


def test_a_name_no_readable_chat_holds_answers_an_empty_list(ask):
    assert found(ask({"action": "find", "name": "Nobody"})) == []


# The account-wide list is a grant of its own, and a search by name must not become one
def test_a_search_by_name_reads_no_dialog_list(ask):
    ask({"action": "find", "name": "club"})
    assert not ask.fake.called("get_dialogs")


def test_a_chat_the_account_cannot_reach_is_skipped(store):
    fake = FakeClient(
        entities={CLUB: Channel(id=2303572307, title="Technical club")},
    )

    async def gone(chat):
        if chat == TEAMMATE:
            raise ValueError("no such entity")
        return fake.entities[chat]

    fake.get_entity = gone
    (store.root / "chats" / f"{TEAMMATE}.conf").write_text("allow: read\n")
    served = handler.Handler(store, client_module.Client(fake))
    answer = asyncio.run(served.handle({"action": "find", "name": "a"}))
    assert found(answer) == [{"chat": CLUB, "name": "Technical club"}]


def test_a_search_without_a_name_is_refused(ask):
    answer = ask({"action": "find"})
    assert answer["ok"] is False
    assert "name" in answer["error"]


def test_a_search_takes_no_chat(ask):
    answer = ask({"action": "find", "chat": CLUB, "name": "club"})
    assert answer["ok"] is False
    assert not ask.fake.called("get_entity")
