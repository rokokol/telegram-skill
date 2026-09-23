"""A message with no text still says what it carries."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


class Carrying(Message):
    """A message whose media Telethon exposes through a named attribute."""

    def __init__(self, id, kind):
        super().__init__(id=id, text="", sender_id=42)
        setattr(self, kind, object())


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / "777.conf").write_text("allow: read\n")
    return permissions.Store(tmp_path)


def ask_with(store, messages, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(messages={777: messages})
    served = handler.Handler(store, client_module.Client(fake))
    return asyncio.run(served.handle({"action": "read", "chat": 777}))


# Without this a sticker, a photo and a voice message are one and the same blank line,
# and a summary of the chat silently drops whatever was not typed
@pytest.mark.parametrize(
    "kind", ["photo", "sticker", "voice", "video", "audio", "document"]
)
def test_an_attachment_is_named_by_kind(store, monkeypatch, kind):
    answer = ask_with(store, [Carrying(1, kind)], monkeypatch)
    assert answer["result"][0]["media"] == kind


def test_a_plain_message_carries_no_media_field(store, monkeypatch):
    answer = ask_with(store, [Message(id=1, text="words", sender_id=42)], monkeypatch)
    assert answer["result"][0]["media"] is None


def test_a_caption_survives_beside_its_attachment(store, monkeypatch):
    carried = Carrying(1, "photo")
    carried.text = "look at this"
    answer = ask_with(store, [carried], monkeypatch)
    assert answer["result"][0]["text"] == "look at this"
    assert answer["result"][0]["media"] == "photo"
