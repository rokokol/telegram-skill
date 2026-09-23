"""The watcher asks for what it has not seen, and every message is one line."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("tg_watch", ROOT / "tg-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class Options:
    def __init__(self, **fields):
        self.chat = "777"
        self.topic = None
        self.socket = "/nowhere"
        self.since = None
        self.interval = 1
        self.once = True
        for name, value in fields.items():
            setattr(self, name, value)


def test_a_message_becomes_exactly_one_line():
    line = watch.line_for({"id": 5, "from": 42, "text": "two\nlines", "out": False})
    assert "\n" not in line
    assert line.startswith("5 42 ")


def test_the_account_own_message_is_an_event_too():
    line = watch.line_for({"id": 6, "from": None, "text": "mine", "out": True})
    assert " self " in line


def test_an_attachment_is_named_by_its_kind():
    line = watch.line_for({"id": 7, "from": 1, "text": "", "out": False, "media": "sticker"})
    assert "[sticker]" in line


def test_a_caption_follows_the_kind_that_carries_it():
    line = watch.line_for(
        {"id": 8, "from": 1, "text": "look", "out": False, "media": "photo"}
    )
    assert line.endswith("[photo] look")


def test_a_message_with_neither_text_nor_media_is_still_a_line():
    line = watch.line_for({"id": 9, "from": 1, "text": "", "out": False, "media": None})
    assert line == "9 1 [empty]"


def test_the_request_asks_from_the_last_seen_rather_than_for_the_last_few():
    request = watch.request_for(Options(), since=100)
    assert request["since"] == 100
    assert request["action"] == "read"


def test_a_first_request_names_no_starting_point():
    assert "since" not in watch.request_for(Options(), since=None)


def test_a_topic_is_carried_into_every_request():
    assert watch.request_for(Options(topic=43628), since=1)["topic"] == 43628


def test_a_round_prints_each_message_and_moves_the_mark(monkeypatch, capsys):
    monkeypatch.setattr(
        watch.tg,
        "ask",
        lambda socket, request: {
            "ok": True,
            "result": [
                {"id": 12, "from": 42, "text": "second", "out": False},
                {"id": 11, "from": 42, "text": "first", "out": False},
            ],
        },
    )
    mark = watch.round_once(Options(), since=10)
    printed = capsys.readouterr().out.splitlines()
    # Oldest first, so a reader sees the conversation in the order it happened
    assert printed == ["11 42 first", "12 42 second"]
    assert mark == 12


def test_a_round_with_nothing_new_keeps_the_mark(monkeypatch):
    monkeypatch.setattr(watch.tg, "ask", lambda socket, request: {"ok": True, "result": []})
    assert watch.round_once(Options(), since=10) == 10


# A permission withdrawn while this runs should stop the output, not the watch: the user
# may put it back, and a watcher that exits on the first refusal has to be noticed and
# restarted by hand
def test_a_refusal_is_reported_without_ending_the_watch(monkeypatch, capsys):
    monkeypatch.setattr(
        watch.tg,
        "ask",
        lambda socket, request: {"ok": False, "error": "chat 777 does not allow read"},
    )
    mark = watch.round_once(Options(), since=10)
    assert "refused" in capsys.readouterr().out
    assert mark == 10


def test_the_newest_of_a_batch_is_its_highest_identifier():
    assert watch.newest([{"id": 3}, {"id": 9}, {"id": 5}]) == 9
    assert watch.newest([]) is None
