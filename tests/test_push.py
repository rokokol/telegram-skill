"""A subscription delivers each new message as it arrives, under the grant that reads it.

The session already receives every update Telegram pushes, so a subscriber gets a
message the moment it lands instead of on the next poll. The grant is decided again for
every message rather than once at the start: a permission withdrawn while a watcher runs
stops the stream at the next message. A subscriber that falls behind is cut off with a
reason, so it catches up by reading rather than the service holding an unbounded queue.
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from telethon.tl import types as t

from tests.fake_telethon import FakeClient, FakeTopic, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions, server


class _Status:
    def __init__(self, offline):
        self.offline = offline


def run(scenario):
    """Run a scenario under a deadline. A stream waits for messages by design, so a
    broken one waits forever, and a test has to turn that into a failure, not a hang.
    A scenario takes milliseconds; the deadline stays short because one defect can stall
    every test here at once, and the defect run gives the whole suite a fixed budget."""
    asyncio.run(asyncio.wait_for(scenario(), timeout=1))


@pytest.fixture
def world(tmp_path, monkeypatch):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _Status)
    fake = FakeClient(
        full_rich={
            9: t.RichMessage(
                blocks=[t.PageBlockParagraph(text=t.TextPlain(text="whole"))],
                photos=[],
                documents=[],
            )
        },
        # The forum these subscriptions name topics of, because a topic is known only
        # through the list the forum itself answers with
        topics=[
            FakeTopic(id=17, title="Work", unread_count=1, top_message=3),
            FakeTopic(id=18, title="Home", unread_count=0, top_message=4),
        ],
    )
    account = client_module.Client(fake)
    service = handler.Handler(permissions.Store(tmp_path), account)

    def grant(chat, words):
        (tmp_path / "chats" / f"{chat}.conf").write_text(f"allow: {words}\n")

    return service, account, fake, grant


async def started(world):
    service, account, fake, grant = world
    await account.start()
    return service, account, fake, grant


def collect(stream, count):
    """The first `count` answers of a stream, then close it."""

    async def take():
        got = []
        async for answer in stream:
            got.append(answer)
            if len(got) == count:
                break
        await stream.aclose()
        return got

    return take()


def test_subscribing_needs_read(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "send")
        answers = await collect(service.stream({"action": "subscribe", "chat": 777}), 1)
        assert answers[0]["ok"] is False
        assert answers[0]["remedy"] == (
            "add read to 'allow: ' in the permission file for chat 777"
        )
        assert account.hub.count(777) == 0

    run(scenario)


def test_a_new_message_in_the_chat_arrives_and_others_do_not(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "read")
        stream = service.stream({"action": "subscribe", "chat": 777})
        ack = await anext(stream)
        assert ack["ok"] is True and ack["allowed_by"] == "read"
        await fake.emit(888, Message(id=4, text="elsewhere", sender_id=5))
        await fake.emit(777, Message(id=5, text="here", sender_id=42))
        event = await anext(stream)
        assert event["ok"] is True
        assert (event["message"]["id"], event["message"]["text"]) == (5, "here")
        await stream.aclose()
        assert account.hub.count(777) == 0

    run(scenario)


def test_the_grant_is_decided_again_for_every_message(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "read")
        stream = service.stream({"action": "subscribe", "chat": 777})
        await anext(stream)
        grant(777, "send")
        await fake.emit(777, Message(id=6, text="after the change", sender_id=42))
        refused = await anext(stream)
        assert refused["ok"] is False
        assert "read" in refused["remedy"]
        assert "after the change" not in json.dumps(refused)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert account.hub.count(777) == 0

    run(scenario)


def test_saved_subscribes_to_the_accounts_own_chat(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(1, "read")
        stream = service.stream({"action": "subscribe", "chat": "saved"})
        assert (await anext(stream))["ok"] is True
        await fake.emit(1, Message(id=7, text="note", sender_id=1, out=True))
        assert (await anext(stream))["message"]["text"] == "note"
        await stream.aclose()

    run(scenario)


def in_topic(top, reply=None):
    """The reply header of a message in a forum topic, posted directly or as a reply."""
    if reply is None:
        return t.MessageReplyHeader(forum_topic=True, reply_to_msg_id=top)
    return t.MessageReplyHeader(forum_topic=True, reply_to_msg_id=reply, reply_to_top_id=top)


def test_a_topic_subscription_carries_only_that_topic(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(-100, "read")
        stream = service.stream({"action": "subscribe", "chat": -100, "topic": 17})
        assert (await anext(stream))["ok"] is True
        await fake.emit(-100, Message(id=1, text="general", sender_id=5))
        await fake.emit(-100, Message(id=2, text="other", sender_id=5, reply_to=in_topic(18)))
        await fake.emit(-100, Message(id=3, text="posted", sender_id=5, reply_to=in_topic(17)))
        await fake.emit(
            -100, Message(id=4, text="answered", sender_id=5, reply_to=in_topic(17, reply=3))
        )
        got = [(await anext(stream))["message"]["text"] for _ in range(2)]
        assert got == ["posted", "answered"]
        await stream.aclose()

    run(scenario)


# A subscription names a topic only through the forum's own list, or a number that names
# nothing would filter every message out and never say why
def test_a_topic_subscription_naming_no_topic_is_refused(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(-100, "read")
        stream = service.stream({"action": "subscribe", "chat": -100, "topic": 4242})
        first = await anext(stream)
        assert first["ok"] is False
        assert "no topic 4242" in first["error"]

    run(scenario)


def test_the_general_topic_holds_what_names_no_topic(world):
    # General is topic 1, and its messages carry no forum header at all
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(-100, "read")
        stream = service.stream({"action": "subscribe", "chat": -100, "topic": 1})
        await anext(stream)
        await fake.emit(-100, Message(id=5, text="topic", sender_id=5, reply_to=in_topic(17)))
        await fake.emit(-100, Message(id=6, text="general", sender_id=5))
        assert (await anext(stream))["message"]["text"] == "general"
        await stream.aclose()

    run(scenario)


def test_a_rich_message_sent_in_part_arrives_whole(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "read")
        stream = service.stream({"action": "subscribe", "chat": 777})
        await anext(stream)
        partial = t.RichMessage(
            blocks=[t.PageBlockParagraph(text=t.TextPlain(text="part"))],
            photos=[],
            documents=[],
            part=True,
        )
        await fake.emit(777, Message(id=9, text="", sender_id=42, rich_message=partial))
        assert (await anext(stream))["message"]["text"] == "whole"
        await stream.aclose()

    run(scenario)


def test_a_subscriber_that_falls_behind_is_told_to_catch_up(world, monkeypatch):
    async def scenario():
        service, account, fake, grant = await started(world)
        monkeypatch.setattr(account.hub, "depth", 2)
        grant(777, "read")
        stream = service.stream({"action": "subscribe", "chat": 777})
        await anext(stream)
        for number in range(1, 5):
            await fake.emit(777, Message(id=number, text=str(number), sender_id=42))
        answers = [answer async for answer in stream]
        assert answers[-1]["ok"] is False
        assert "behind" in answers[-1]["error"]
        assert account.hub.count(777) == 0

    run(scenario)


# Over the socket


def test_the_socket_streams_events_and_lets_go_when_the_reader_leaves(world, tmp_path):
    path = str(tmp_path / "s")

    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "read")
        served = asyncio.ensure_future(server.Server(service).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(b'{"action": "subscribe", "chat": 777}\n')
        await writer.drain()
        ack = json.loads(await reader.readline())
        assert ack["ok"] is True
        await fake.emit(777, Message(id=11, text="live", sender_id=42))
        event = json.loads(await reader.readline())
        assert event["message"]["text"] == "live"
        assert account.hub.count(777) == 1
        writer.close()
        # The service notices the reader has gone without waiting for another message
        for _ in range(100):
            if account.hub.count(777) == 0:
                break
            await asyncio.sleep(0.01)
        assert account.hub.count(777) == 0
        served.cancel()

    run(scenario)


def test_the_client_reads_a_live_stream_line_by_line(world, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tg_client_push", Path(__file__).resolve().parent.parent / "tg.py"
    )
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    path = str(tmp_path / "s")

    async def scenario():
        service, account, fake, grant = await started(world)
        grant(777, "read")
        served = asyncio.ensure_future(server.Server(service).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        answers = tg.stream(path, {"action": "subscribe", "chat": 777})
        ack = await asyncio.to_thread(next, answers)
        assert ack["ok"] is True
        pending = asyncio.ensure_future(asyncio.to_thread(next, answers))
        while account.hub.count(777) == 0:
            await asyncio.sleep(0.01)
        await fake.emit(777, Message(id=12, text="through tg.py", sender_id=42))
        assert (await pending)["message"]["text"] == "through tg.py"
        answers.close()
        served.cancel()

    # tg.stream blocks in a worker thread, which the deadline cannot cancel and which
    # the loop waits for on its way out, so a socket timeout is what ends a failed run
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(1)
    try:
        run(scenario)
    finally:
        socket.setdefaulttimeout(previous)


def test_an_ordinary_request_on_the_socket_still_gets_one_answer(world, tmp_path):
    path = str(tmp_path / "s")

    async def scenario():
        service, account, fake, grant = await started(world)
        served = asyncio.ensure_future(server.Server(service).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)

        def exchange():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(path)
                connection.sendall(b'{"action": "permissions"}\n')
                return json.loads(connection.makefile().readline())

        answer = await asyncio.to_thread(exchange)
        assert answer["ok"] is True
        served.cancel()

    run(scenario)


def test_starting_registers_for_new_messages_once(world):
    async def scenario():
        service, account, fake, grant = await started(world)
        assert [c["event"] for c in fake.called("add_event_handler")] == ["NewMessage"]

    run(scenario)
