"""Downloads land inside the outbox and nowhere else."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, FakeFile, Message
from tg_agentd import client as client_module
from tg_agentd import handler, media, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def outbox(tmp_path):
    path = tmp_path / "outbox"
    path.mkdir()
    return media.Outbox(path)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store, outbox, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(
        messages={
            777: [
                Message(id=1, text="", sender_id=42, file=FakeFile("Отчет.pdf", ".pdf")),
                Message(id=2, text="", sender_id=42, file=FakeFile(None, ".jpg")),
                Message(id=3, text="words only", sender_id=42),
            ]
        }
    )
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


@pytest.mark.parametrize("name", ["Отчет_НИР.pdf", "日本語.txt", "Ünïcödé-файл.docx"])
def test_a_name_in_any_alphabet_keeps_its_letters(name):
    assert media.component(name) == name


# Written as a code point, so the source holds no character that reorders what it shows
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
FORBIDDEN = {"/", "\\", "\0", "\n", RIGHT_TO_LEFT_OVERRIDE}


@pytest.mark.parametrize("name", ["Отчет/../x", "a\\b", "a\0b", "a\nb", f"a{RIGHT_TO_LEFT_OVERRIDE}b"])
def test_a_name_loses_separators_and_controls_whatever_its_alphabet(name):
    assert not set(media.component(name)) & FORBIDDEN


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


def test_a_download_keeps_the_name_the_document_carries(ask, store):
    grant(store, 777, "media")
    answer = ask({"action": "media", "chat": 777, "ids": [1]})
    assert [path.rsplit("/", 1)[-1] for path in answer["result"]["written"]] == ["Отчет.pdf"]


def test_an_attachment_with_no_name_is_named_by_its_message_and_kind(ask, store):
    grant(store, 777, "media")
    answer = ask({"action": "media", "chat": 777, "ids": [2]})
    assert [path.rsplit("/", 1)[-1] for path in answer["result"]["written"]] == ["2.jpg"]


def test_a_message_that_is_not_there_is_reported_rather_than_downloaded(ask, store):
    grant(store, 777, "media")
    answer = ask({"action": "media", "chat": 777, "ids": [1, 99]})
    assert answer["result"]["missing"] == [99]
    assert len(answer["result"]["written"]) == 1
    assert all(call["message"] is not None for call in ask.fake.called("download_media"))


def test_a_message_with_nothing_attached_is_reported_rather_than_downloaded(ask, store):
    grant(store, 777, "media")
    answer = ask({"action": "media", "chat": 777, "ids": [3]})
    assert answer["result"] == {"written": [], "missing": [], "no_file": [3]}
    assert not ask.fake.called("download_media")


def test_a_service_with_no_outbox_refuses_rather_than_inventing_a_path(store):
    served = handler.Handler(store, client_module.Client(FakeClient()), outbox=None)
    grant(store, 777, "media")
    answer = asyncio.run(served.handle({"action": "media", "chat": 777, "ids": [1]}))
    assert answer["ok"] is False
    assert "outbox" in answer["error"]
