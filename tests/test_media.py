"""Downloads land inside the outbox and nowhere else, and they expire."""

import asyncio
import time

import pytest

from tests.fake_telethon import FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, media, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def outbox(tmp_path):
    path = tmp_path / "outbox"
    path.mkdir()
    return media.Outbox(path, keep_for=3600)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store, outbox, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(messages={777: [Message(id=1, text="file", sender_id=42)]})
    served = handler.Handler(store, client_module.Client(fake), outbox=outbox)

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def grant(store, chat, words):
    (store.root / "chats" / f"{chat}.conf").write_text(f"allow: {words}\n")


@pytest.mark.parametrize(
    "name", ["../escape", "/etc", "a/../..", "..", "", ".", "a\0b", "a/b/../../.."]
)
def test_a_destination_outside_the_outbox_is_refused(outbox, name):
    with pytest.raises(media.BadDestination):
        outbox.place(name, "file.jpg")


def test_an_ordinary_name_lands_inside_the_outbox(outbox):
    path = outbox.place("chat-777", "photo.jpg")
    assert path.parent.parent == outbox.root
    assert str(path).startswith(str(outbox.root))


def test_a_file_name_from_the_far_side_cannot_escape_either(outbox):
    # The name of an attachment is chosen by whoever sent it, so it is hostile input
    path = outbox.place("chat-777", "../../../etc/passwd")
    assert str(path).startswith(str(outbox.root))
    assert "passwd" in path.name


def test_expired_files_are_removed_and_fresh_ones_are_kept(tmp_path):
    root = tmp_path / "outbox"
    root.mkdir()
    box = media.Outbox(root, keep_for=60)
    old = root / "old.bin"
    old.write_bytes(b"x")
    fresh = root / "fresh.bin"
    fresh.write_bytes(b"x")
    stale = time.time() - 3600
    import os

    os.utime(old, (stale, stale))
    removed = box.sweep()
    assert old.name in removed
    assert not old.exists()
    assert fresh.exists()


def test_downloading_needs_its_own_grant(ask, store):
    grant(store, 777, "read")
    answer = ask({"action": "media", "chat": 777, "ids": [1]})
    assert answer["ok"] is False
    assert not ask.fake.called("download_media")


def test_downloading_never_marks_the_chat_read(ask, store):
    grant(store, 777, "media")
    answer = ask({"action": "media", "chat": 777, "ids": [1]})
    assert answer["ok"] is True
    assert ask.fake.called("download_media")
    assert not ask.fake.called("send_read_acknowledge")


def test_a_service_with_no_outbox_refuses_rather_than_inventing_a_path(store):
    served = handler.Handler(store, client_module.Client(FakeClient()), outbox=None)
    grant(store, 777, "media")
    answer = asyncio.run(served.handle({"action": "media", "chat": 777, "ids": [1]}))
    assert answer["ok"] is False
    assert "outbox" in answer["error"]
