"""A forum holds its messages in topics, and each topic is read on its own."""

import asyncio

import pytest
from telethon.tl import types as t

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
    in_work = t.MessageReplyHeader(reply_to_msg_id=17, forum_topic=True)
    fake = FakeClient(
        messages={
            CHAT: [
                Message(id=1, text="in a topic", sender_id=9),
                Message(id=900, text="in Work", sender_id=9, reply_to=in_work),
                Message(id=950, text="among the chat's own", sender_id=9),
            ]
        },
        **fake,
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


# The forum's own list is the only thing that says which numbers are topics. A number
# handed straight to Telegram asks for the thread of whatever message carries it, so a
# number that names no topic answers with another conversation instead of a refusal
def test_a_topic_read_by_number_is_held_to_the_forums_list(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": 4242})
    assert answer["ok"] is False
    assert "no topic 4242" in answer["error"]
    assert not ask.fake.called("get_messages")


def test_a_topic_read_by_number_asks_for_that_topic(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": 17})
    assert answer["ok"] is True, answer
    named = [
        called
        for called in ask.fake.called("raw")
        if called["request"] == "GetForumTopicsByIDRequest" and "topics" in called
    ]
    assert named and named[0]["topics"] == [17], named


# The list answers with a page, and a forum may hold more topics than a page carries: the
# number names the topic itself, so the rest of the forum is read as surely as its first
# page, and a message's own `topic` is how a caller learns a number the page never showed
def test_a_topic_the_page_does_not_show_is_read_by_its_number(store):
    ask = serve(
        store,
        topics=[FakeTopic(id=17, title="Work", unread_count=0, top_message=9)],
        held_topics=[
            FakeTopic(id=17, title="Work", unread_count=0, top_message=9),
            FakeTopic(id=9001, title="Deep", unread_count=2, top_message=9600),
        ],
    )
    answer = ask({"action": "read", "chat": CHAT, "topic": 9001})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")[0]["reply_to"] == 9001


def test_a_title_past_the_page_is_found_by_the_forums_own_search(store):
    ask = serve(
        store,
        topics=[FakeTopic(id=17, title="Work", unread_count=0, top_message=9)],
        held_topics=[
            FakeTopic(id=17, title="Work", unread_count=0, top_message=9),
            FakeTopic(id=9001, title="Deep", unread_count=2, top_message=9600),
        ],
    )
    answer = ask({"action": "read", "chat": CHAT, "topic": "Deep"})
    assert answer["ok"] is True, answer
    assert ask.fake.called("get_messages")[0]["reply_to"] == 9001


# General is the chat's own messages rather than a thread: no message starts it, so asking
# Telegram for message 1's thread answers whatever that number happens to point at, and in
# a bot's chat it answers a library error instead
def test_general_is_not_read_as_a_thread(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": 1})
    assert answer["ok"] is False
    assert "General" in answer["error"]
    assert not ask.fake.called("get_messages")


def test_general_is_not_read_as_a_thread_by_its_title(ask):
    answer = ask({"action": "read", "chat": CHAT, "topic": "general"})
    assert answer["ok"] is False
    assert "General" in answer["error"]
    assert not ask.fake.called("get_messages")


# General is the chat's own position, and a forum answers for it like any other topic
def test_general_moves_the_chats_own_position(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 1})
    assert answer["ok"] is True, answer
    assert answer["result"]["general"] is True
    assert ask.fake.called("send_read_acknowledge")
    assert not ask.fake.called("raw_topic_read")


# A number is answered by the topic it names, so a chat that keeps no topics refuses even
# when the number came from somewhere else
def test_a_number_in_a_chat_that_is_no_forum_says_so(ask, monkeypatch):
    async def not_a_forum(*args, **kwargs):
        raise RuntimeError("ChannelForumMissingError:  (caused by GetForumTopicsByIDRequest)")

    monkeypatch.setattr(client_module.Client, "topic", not_a_forum)
    answer = ask({"action": "read", "chat": CHAT, "topic": 9001})
    assert answer["ok"] is False
    assert "keeps no topics" in answer["error"]


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
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 1, "id": 950})
    assert answer["ok"] is True, answer
    assert ask.fake.called("send_read_acknowledge") == [
        {"entity": CHAT, "message": None, "max_id": 950}
    ]
    assert answer["result"]["read_max_id"] == 950


# A number past the newest message would be clamped by the server to the newest one, so
# the whole chat would read as seen though the agent saw nothing of it
def test_a_read_past_the_newest_message_is_refused(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "id": 999999})
    assert answer["ok"] is False
    assert "is not one chat" in answer["error"]
    assert not ask.fake.called("send_read_acknowledge")
    assert not ask.fake.called("raw_topic_read")


# A topic read stops at a message of that topic, not at one of another thread
def test_a_topic_read_refuses_a_message_of_another_topic(ask):
    answer = ask({"action": "mark-read", "chat": CHAT, "topic": 17, "id": 950})
    assert answer["ok"] is False
    assert "in no topic 17" in answer["error"]
    assert not ask.fake.called("raw_topic_read")


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
