"""A read says when, who, what a message answers, and what a poll counted.

A summary of a chat is a summary of who said what and when. Without a date, a name, the
message being answered and the votes of a poll, a reader receives sentences with no
speaker and decisions with no tally, and fills the gaps by guessing.
"""

import asyncio
import datetime as dt

import pytest
from telethon.tl import types as t

from tests.fake_telethon import FakeClient, FakeFile, Message, User
from tg_agentd import client as client_module
from tg_agentd import handler, permissions

CHAT = -1001234567890
YES, NO = b"\x00", b"\x01"


def poll(public):
    return t.MessageMediaPoll(
        poll=t.Poll(
            id=1,
            hash=0,
            question=t.TextWithEntities(text="Meet on Friday?", entities=[]),
            answers=[
                t.PollAnswer(
                    text=t.TextWithEntities(text="yes", entities=[]), option=YES
                ),
                t.PollAnswer(text=t.TextWithEntities(text="no", entities=[]), option=NO),
            ],
            public_voters=public,
        ),
        results=t.PollResults(
            results=[
                t.PollAnswerVoters(option=YES, voters=2),
                t.PollAnswerVoters(option=NO, voters=1),
            ],
            total_voters=3,
        ),
    )


VOTES = t.messages.VotesList(
    count=3,
    votes=[
        t.MessagePeerVote(peer=t.PeerUser(user_id=1), option=YES, date=None),
        t.MessagePeerVote(peer=t.PeerUser(user_id=2), option=YES, date=None),
        t.MessagePeerVoteMultiple(peer=t.PeerUser(user_id=3), options=[NO], date=None),
    ],
    chats=[],
    users=[
        User(id=1, first_name="Ann"),
        User(id=2, first_name="Bo", last_name="Li"),
        User(id=3, username="cy"),
    ],
)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / f"{CHAT}.conf").write_text("allow: read\n")
    return permissions.Store(tmp_path)


def read(store, messages, **fake):
    served = handler.Handler(
        store, client_module.Client(FakeClient(messages={CHAT: messages}, **fake))
    )
    answer = asyncio.run(served.handle({"action": "read", "chat": CHAT}))
    assert answer["ok"] is True, answer
    return answer["result"]


def test_date_and_sender_name(store):
    when = dt.datetime(2026, 10, 28, 9, 30, tzinfo=dt.timezone.utc)
    [message] = read(
        store,
        [
            Message(
                id=5,
                text="hi",
                sender_id=7,
                date=when,
                sender=User(id=7, first_name="Ann", last_name="Lee"),
            )
        ],
    )
    assert message["date"] == "2026-10-28T09:30:00+00:00"
    assert message["from"] == 7
    assert message["from_name"] == "Ann Lee"


def test_a_reply_names_the_message_it_answers_but_a_topic_post_does_not(store):
    answering = t.MessageReplyHeader(reply_to_msg_id=4)
    in_topic = t.MessageReplyHeader(reply_to_msg_id=100, forum_topic=True)
    answering_in_topic = t.MessageReplyHeader(
        reply_to_msg_id=4, forum_topic=True, reply_to_top_id=100
    )
    got = read(
        store,
        [
            Message(id=5, text="a", sender_id=7, reply_to=answering),
            Message(id=6, text="b", sender_id=7, reply_to=in_topic),
            Message(id=7, text="c", sender_id=7, reply_to=answering_in_topic),
        ],
    )
    assert [m["reply_to"] for m in got] == [4, None, 4]


def test_a_forward_names_where_it_came_from(store):
    named = t.MessageFwdHeader(date=None, from_name="Hidden Person")
    from_channel = t.MessageFwdHeader(date=None, from_id=t.PeerChannel(channel_id=55))
    got = read(
        store,
        [
            Message(id=5, text="a", sender_id=7, fwd_from=named),
            Message(id=6, text="b", sender_id=7, fwd_from=from_channel),
            Message(id=7, text="c", sender_id=7),
        ],
    )
    assert [m["forwarded_from"] for m in got] == ["Hidden Person", -1000000000055, None]


def test_a_voice_note_and_a_round_video_say_how_long_they_are(store):
    voice = FakeFile(None, ".ogg")
    voice.duration = 12
    got = read(
        store,
        [
            Message(id=5, text="", sender_id=7, file=voice, video_note=None),
            Message(id=6, text="", sender_id=7, file=voice, video_note=object()),
        ],
    )
    assert got[1]["media"] == "round"
    assert got[1]["duration"] == 12


def test_an_anonymous_poll_counts_and_asks_for_no_names(store):
    fake = FakeClient(
        messages={CHAT: [Message(id=5, text="", sender_id=7, poll=poll(public=False))]}
    )
    served = handler.Handler(store, client_module.Client(fake))
    [message] = asyncio.run(served.handle({"action": "read", "chat": CHAT}))["result"]
    assert message["media"] == "poll"
    assert message["poll"]["question"] == "Meet on Friday?"
    assert [(a["text"], a["votes"]) for a in message["poll"]["answers"]] == [
        ("yes", 2),
        ("no", 1),
    ]
    assert message["poll"]["total"] == 3
    assert not any("voters" in a for a in message["poll"]["answers"])
    assert not fake.called("raw_poll_votes")


def test_a_public_poll_names_who_chose_what(store):
    [message] = read(
        store,
        [Message(id=5, text="", sender_id=7, poll=poll(public=True))],
        poll_votes={5: VOTES},
    )
    voters = {a["text"]: a.get("voters") for a in message["poll"]["answers"]}
    assert voters == {"yes": ["Ann", "Bo Li"], "no": ["cy"]}


def test_a_refused_voter_list_leaves_the_counts_standing(store):
    [message] = read(store, [Message(id=5, text="", sender_id=7, poll=poll(public=True))])
    assert message["poll"]["total"] == 3
    assert "voters_error" in message["poll"]
