"""Listing folders needs no grant, because a grant cannot be written without an id."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, FakeFolder
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
        folders=[
            FakeFolder(id=5, title="study", include_peers=[777, 888]),
            FakeFolder(id=9, title="work", include_peers=[111]),
        ]
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


# Telegram shows no folder identifier anywhere in its interface, so without this the
# permission for a folder could never be written in the first place
def test_folders_are_listed_without_any_grant(ask):
    answer = ask({"action": "folder-list"})
    assert answer["ok"] is True
    listed = {f["id"]: f for f in answer["result"]["folders"]}
    assert listed[5]["title"] == "study"
    assert listed[9]["title"] == "work"


def test_the_listing_counts_members_without_naming_them(ask):
    # The count is what tells a grant apart from a mistake; the names are content, and
    # content is what the grant is for
    answer = ask({"action": "folder-list"})
    listed = {f["id"]: f for f in answer["result"]["folders"]}
    assert listed[5]["chats"] == 2
    assert "members" not in listed[5]


def test_reading_a_folder_still_needs_its_own_grant(ask):
    assert ask({"action": "folder-read", "folder": 5})["ok"] is False
