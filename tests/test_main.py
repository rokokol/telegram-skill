"""The service is assembled from its command line the way the unit starts it."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, FakeFile, Message
from tg_agentd import __main__ as entry
from tg_agentd import client as client_module


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def permissions_dir(tmp_path):
    root = tmp_path / "permissions"
    (root / "chats").mkdir(parents=True)
    (root / "folders").mkdir()
    (root / "chats" / "777.conf").write_text("allow: media\n")
    return root


def ask(options, monkeypatch, request):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(
        messages={777: [Message(id=1, text="", sender_id=42, file=FakeFile("a.pdf", ".pdf"))]}
    )
    served = entry.build_handler(options, client_module.Client(fake))
    return asyncio.run(served.handle(request))


def test_media_dir_on_the_command_line_is_where_downloads_land(
    tmp_path, permissions_dir, monkeypatch
):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    options = entry.build_parser().parse_args(
        ["--permissions", str(permissions_dir), "--media-dir", str(outbox)]
    )
    answer = ask(options, monkeypatch, {"action": "media", "chat": 777, "ids": [1]})
    assert answer["ok"] is True, answer
    written = answer["result"]["written"]
    assert written and all(path.startswith(str(outbox)) for path in written)


def test_without_media_dir_a_download_is_refused(permissions_dir, monkeypatch):
    options = entry.build_parser().parse_args(["--permissions", str(permissions_dir)])
    answer = ask(options, monkeypatch, {"action": "media", "chat": 777, "ids": [1]})
    assert answer["ok"] is False
    assert "outbox" in answer["error"]
