"""A forum holds its messages in topics, and each topic is read on its own."""

import asyncio

import pytest

from tests.fake_telethon import FakeClient, FakeTopic, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / "-1002303572307.conf").write_text("allow: read\n")
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(
        messages={-1002303572307: [Message(id=1, text="in a topic", sender_id=9)]},
        topics=[
            FakeTopic(id=1, title="General", unread_count=0),
            FakeTopic(id=17, title="Work", unread_count=4),
        ],
    )
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


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
