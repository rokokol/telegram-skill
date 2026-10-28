import asyncio
from types import SimpleNamespace as NS

import pytest
from telethon.tl.types import ReactionCount, ReactionCustomEmoji, ReactionEmoji, ReactionPaid

import tg
from tests.fake_telethon import FakeClient, FakeFolder, Message
from tg_agentd.client import Client
from tg_agentd.handler import Handler
from tg_agentd.permissions import Store


@pytest.fixture
def setup(tmp_path):
    for kind in ("chats", "folders"):
        (tmp_path / kind).mkdir()
    message = Message(id=12, text="hello", sender_id=42)
    message.reactions = NS(results=[
        ReactionCount(ReactionEmoji("👍"), 3, chosen_order=0),
        ReactionCount(ReactionCustomEmoji(123), 2),
        ReactionCount(ReactionPaid(), 7),
    ])
    fake = FakeClient(messages={777: [message]}, folders=[
        FakeFolder(id=5, title="study", include_peers=[777]),
    ])
    served = Handler(Store(tmp_path), Client(fake))

    def grant(words, kind="chats", target=777):
        (tmp_path / kind / f"{target}.conf").write_text(f"allow: {words}\n")

    def ask(action, **fields):
        return asyncio.run(served.handle({"action": action, "chat": 777, **fields}))

    return grant, ask, fake


@pytest.mark.parametrize("kind,target", [("chats", 777), ("folders", 5)])
def test_read_includes_reactions(setup, kind, target):
    grant, ask, fake = setup
    grant("read", kind, target)
    answer = ask("read", ids=[12])
    assert answer["ok"], answer
    assert answer["result"][0]["reactions"] == [
        {"emoji": "👍", "count": 3, "chosen": True},
        {"custom_emoji_id": 123, "count": 2, "chosen": False},
        {"paid": True, "count": 7, "chosen": False},
    ]
    assert fake.called("get_messages")[-1]["ids"] == [12]
    assert not fake.called("send_read_acknowledge")


@pytest.mark.parametrize("kind,target", [("chats", 777), ("folders", 5)])
@pytest.mark.parametrize("reactions", [["👍"], []])
def test_write_sets_or_clears_reactions(setup, kind, target, reactions):
    grant, ask, fake = setup
    grant("write", kind, target)
    answer = ask("react", id=12, reactions=reactions)
    assert answer["ok"], answer
    assert answer["allowed_by"] == "write"
    assert fake.called("raw_reaction") == [
        {"peer": 777, "msg_id": 12, "reactions": reactions}
    ]
    assert not fake.called("send_read_acknowledge")
    if kind == "folders":
        assert answer["through_folder"]["id"] == 5


@pytest.mark.parametrize("words", ["", "read", "send", "reply"])
def test_reacting_needs_write(setup, words):
    grant, ask, fake = setup
    grant(words)
    answer = ask("react", id=12, reactions=["👍"])
    assert not answer["ok"]
    assert "write" in answer["remedy"]
    assert not fake.called("raw_reaction")


def test_write_does_not_allow_reading_or_sending(setup):
    grant, ask, fake = setup
    grant("write")
    assert not ask("read")["ok"]
    assert not ask("send", text="hello")["ok"]
    assert not fake.called("get_messages")
    assert not fake.called("send_message")


def test_folder_write_does_not_reach_an_outsider_or_allow_folder_edits(setup):
    grant, ask, fake = setup
    grant("write, folder-edit", "folders", 5)
    assert not ask("react", chat=888, id=12, reactions=["👍"])["ok"]
    assert not ask("folder-edit", folder=5, add=[888])["ok"]
    assert not fake.called("raw_reaction")
    assert not fake.called("raw_update_filter")


@pytest.mark.parametrize("fields", [{"id": 12}, {"reactions": ["👍"]},
    {"id": 12, "reactions": "👍"}, {"id": 12, "reactions": [""]}])
def test_incomplete_reaction_requests_never_clear_reactions(setup, fields):
    grant, ask, fake = setup
    grant("write")
    assert not ask("react", **fields)["ok"]
    assert not fake.called("raw_reaction")


def test_cli_preserves_explicit_reaction_removal():
    options = tg.build_parser().parse_args([
        "react", "--chat", "777", "--id", "12", "--reactions",
    ])
    assert tg.request_from(options)["reactions"] == []


@pytest.mark.parametrize("kind,target", [
    ("chats", 777), ("chats", "default"),
    ("folders", 5), ("folders", "default"),
])
@pytest.mark.parametrize("words", ["write", "all"])
def test_reaction_grants_include_defaults_and_wildcards(setup, kind, target, words):
    grant, ask, fake = setup
    grant(words, kind, target)
    answer = ask("react", id=12, reactions=["🔥", "👍"])
    assert answer["ok"], answer
    assert answer["allowed_by"] == words
    assert fake.called("raw_reaction")[-1]["reactions"] == ["🔥", "👍"]
    grant("read", kind, target)
    assert not ask("react", id=12, reactions=[])["ok"]
    assert len(fake.called("raw_reaction")) == 1


def test_messages_without_reactions_have_an_empty_list(setup):
    grant, ask, fake = setup
    grant("read")
    fake.messages[777].append(Message(id=13, text="plain", sender_id=42))
    assert ask("read", ids=[13])["result"][0]["reactions"] == []


def test_cli_passes_reaction_choices():
    options = tg.build_parser().parse_args([
        "react", "--chat", "777", "--id", "12", "--reactions", "👍", "🔥",
    ])
    assert tg.request_from(options) == {
        "action": "react", "chat": "777", "id": 12, "reactions": ["👍", "🔥"],
    }
