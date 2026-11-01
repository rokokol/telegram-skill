"""A forum holds its messages in topics, and each topic is read on its own."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, FakeTopic, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


CHAT = -1002303572307


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / f"{CHAT}.conf").write_text("allow: read, mark-read\n")
    return permissions.Store(tmp_path)


def serve(store, **fake):
    """A handler over a stand-in holding one forum, and the calls it made."""
    fake = FakeClient(
        messages={CHAT: [Message(id=1, text="in a topic", sender_id=9)]}, **fake
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


@pytest.fixture
def ask(store, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    return serve(
        store,
        topics=[
            FakeTopic(id=1, title="General", unread_count=0),
            FakeTopic(id=17, title="Work", unread_count=4, top_message=967),
        ],
    )


# A forum's messages all sit in topics, so reading it without naming one returns every
# topic interleaved — which reads as noise and hides which conversation is which
def test_a_forum_lists_its_topics(ask):
    answer = ask({"action": "topics", "chat": -1002303572307})
    assert answer["ok"] is True, answer
    listed = {t["id"]: t for t in answer["result"]["topics"]}
    assert listed[17]["title"] == "Work"
    assert listed[17]["unread"] == 4


def test_listing_topics_needs_read_on_the_chat(ask, store):
    answer = ask({"action": "topics", "chat": 999})
    assert answer["ok"] is False


def test_a_topic_is_read_on_its_own(ask):
    ask({"action": "read", "chat": -1002303572307, "topic": 17})
    assert ask.fake.called("get_messages")[0]["reply_to"] == 17


def test_a_read_without_a_topic_names_none(ask):
    ask({"action": "read", "chat": -1002303572307})
    assert "reply_to" not in ask.fake.called("get_messages")[0]


def test_a_topic_read_still_marks_nothing(ask):
    ask({"action": "read", "chat": -1002303572307, "topic": 17})
    assert not ask.fake.called("send_read_acknowledge")


def test_a_search_inside_one_topic_stays_inside_it(ask):
    ask({"action": "read", "chat": -1002303572307, "topic": 17, "search": "x"})
    call = ask.fake.called("get_messages")[0]
    assert call["reply_to"] == 17
    assert call["search"] == "x"


# A topic is known by its name in the app and by its number nowhere, so a read that
# names a title must reach the same topic a number does
def test_a_topic_can_be_read_by_its_title(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": "Work"})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")[0]["reply_to"] == 17


def test_a_title_matches_whatever_its_case(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": "wOrK"})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")[0]["reply_to"] == 17


# A number is an identifier rather than a title, so a read by number costs no extra
# request: the list of topics is only there to turn a title into one
def test_a_topic_read_by_number_asks_for_no_topic_list(ask):
    ask({"action": "read", "chat": CHAT, "topic": 17})
    listed = [
        called
        for called in ask.fake.called("raw")
        if called["request"] == "GetForumTopicsRequest"
    ]
    assert listed == []


def test_a_title_the_forum_lacks_is_refused_whole(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": "Play"})
    assert answer["ok"] is False
    assert "no topic titled" in answer["error"]
    assert "ValueError" not in answer["error"]


def test_a_title_two_topics_share_is_refused_by_number(store):
    ask = serve(
        store,
        topics=[
            FakeTopic(id=17, title="Work", unread_count=4, top_message=967),
            FakeTopic(id=23, title="Work", unread_count=1, top_message=41),
        ],
    )
    answer = ask({"action": "read", "chat": CHAT, "topic": "Work"})
    assert answer["ok"] is False
    assert "17" in answer["error"] and "23" in answer["error"]
    assert not ask.fake.called("get_messages")


# The counter a channel-wide read leaves standing belongs to the topic, so the call that
# moves it names the topic as a thread and the last message read inside it
def test_marking_a_topic_read_names_the_topic_as_a_thread(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 17})
    assert answer["ok"] is True, answer
    assert ask.fake.called("raw_topic_read") == [
        {"peer": CHAT, "msg_id": 17, "read_max_id": 967}
    ]
    assert not ask.fake.called("send_read_acknowledge")
    assert answer["result"]["read_max_id"] == 967


def test_marking_a_topic_read_takes_the_topic_by_title(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": "Work"})
    assert answer["ok"] is True, answer
    assert ask.fake.called("raw_topic_read")[0]["msg_id"] == 17


def test_marking_a_topic_read_can_stop_at_one_message(ask):
    ask({"action": "mark-read", "chat": CHAT, "topic": 17, "id": 900})
    assert ask.fake.called("raw_topic_read")[0]["read_max_id"] == 900


# General carries no forum header, so it is the chat's own read position rather than a
# thread of its own
def test_the_general_topic_moves_the_chats_own_read_position(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 1})
    assert answer["ok"] is True, answer
    assert ask.fake.called("send_read_acknowledge")
    assert not ask.fake.called("raw_topic_read")
    assert answer["result"]["general"] is True


# General moves the chat's own read position, which can stop at a message too
def test_the_general_topic_can_stop_at_one_message(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 1, "id": 900})
    assert answer["ok"] is True, answer
    assert ask.fake.called("send_read_acknowledge") == [
        {"entity": CHAT, "message": None, "max_id": 900}
    ]
    assert answer["result"]["read_max_id"] == 900


def test_marking_a_topic_read_needs_mark_read(ask, store):
    (store.root / "chats" / f"{CHAT}.conf").write_text("allow: read\n")
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 17})
    assert answer["ok"] is False
    assert "mark-read" in answer["remedy"]
    assert not ask.fake.called("raw_topic_read")


def test_a_topic_holding_no_message_is_refused(store):
    ask = serve(store, topics=[FakeTopic(id=17, title="Empty", unread_count=0)])
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 17})
    assert answer["ok"] is False
    assert "holds no message" in answer["error"]
    assert not ask.fake.called("raw_topic_read")


def test_a_topic_of_a_chat_that_is_no_forum_says_so(ask, monkeypatch):
    async def not_a_forum(*args, **kwargs):
        raise RuntimeError("ChannelForumMissingError:  (caused by GetForumTopicsRequest)")

    monkeypatch.setattr(client_module.Client, "topics", not_a_forum)
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": "Work"})
    assert answer["ok"] is False
    assert "keeps no topics" in answer["error"]


# A chat that is simply not a forum is an ordinary answer, not a fault. The library's own
# error names a constructor nobody outside it has heard of
def test_a_chat_that_is_no_forum_says_so_plainly(ask, store, monkeypatch):
    async def not_a_forum(*args, **kwargs):
        raise RuntimeError("ChannelForumMissingError:  (caused by GetForumTopicsRequest)")

    monkeypatch.setattr(client_module.Client, "topics", not_a_forum)
    (store.root / "chats" / "777.conf").write_text("allow: read\n")
    answer = ask({"action": "topics", "chat": 777})
    assert answer["ok"] is False
    assert "keeps no topics" in answer["error"]
    assert "ChannelForumMissing" not in answer["error"]


# A chat that is no forum at all answers with a constructor name of its own, and half the
# chats in an account are one of these: a basic group, a person, a channel without access
@pytest.mark.parametrize(
    "name", ["ChannelInvalidError", "ChannelPrivateError", "PeerIdInvalidError"]
)
def test_every_shape_of_no_topics_says_so_plainly(ask, store, monkeypatch, name):
    refusal = type(name, (Exception,), {})

    async def no_topics(*args, **kwargs):
        raise refusal()

    monkeypatch.setattr(client_module.Client, "topics", no_topics)
    (store.root / "chats" / "777.conf").write_text("allow: read\n")
    answer = ask({"action": "topics", "chat": 777})
    assert answer["ok"] is False
    assert "keeps no topics" in answer["error"]
    assert name not in answer["error"]
