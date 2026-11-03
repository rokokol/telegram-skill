"""The watcher runs under any hook: no echo of the agent's own sends, a mark kept between
runs, and a command run for each new message.

A message the agent sent is something it already knows, so the watcher drops it; a
message the user sent from a phone is news, so it stays. The two differ only in who sent
them through this client, which is why tg.py remembers what it sent. A hook starts the
watcher fresh each time, so the mark it resumes from lives in a file.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("tg_watch_hook", ROOT / "tg-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
tg = watch.tg


class Options:
    def __init__(self, **fields):
        self.chat = "777"
        self.topic = None
        self.socket = "/nowhere"
        self.since = None
        self.interval = 1
        self.once = True
        self.state = None
        self.exec = None
        for name, value in fields.items():
            setattr(self, name, value)


def serve(monkeypatch, messages, sent=None):
    """Answer every read with these messages, and every send with message 50."""
    asked = []

    def ask(socket, request, deadline=None):
        asked.append(request)
        if request["action"] in ("send", "reply"):
            return {"ok": True, "result": {"id": sent or 50, "out": True}}
        if request["action"] == "read" and request.get("limit") == 1:
            return {"ok": True, "result": messages[-1:]}
        # The service asks the server for min_id, so nothing at or below the mark returns
        since = request.get("since")
        return {
            "ok": True,
            "result": [m for m in messages if since is None or m["id"] > since],
        }

    monkeypatch.setattr(tg, "ask", ask)
    return asked


def test_the_agents_own_send_is_not_echoed_but_the_users_is(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TG_SENT_LOG", str(tmp_path / "sent"))
    serve(monkeypatch, [], sent=21)
    assert tg.main(["send", "--chat", "777", "--text", "from the agent"]) == 0
    capsys.readouterr()

    serve(
        monkeypatch,
        [
            {"id": 21, "from": 1, "text": "from the agent", "out": True},
            {"id": 22, "from": 1, "text": "from the phone", "out": True},
            {"id": 23, "from": 42, "text": "from the friend", "out": False},
        ],
    )
    watch.round_once(Options(), since=20)
    printed = capsys.readouterr().out.splitlines()
    assert printed == ["22 self from the phone", "23 42 from the friend"]


def test_a_send_is_remembered_per_chat(monkeypatch, tmp_path, capsys):
    # Message identifiers repeat across channels, so a send in one chat hides nothing
    # in another
    monkeypatch.setenv("TG_SENT_LOG", str(tmp_path / "sent"))
    serve(monkeypatch, [], sent=21)
    tg.main(["send", "--chat", "888", "--text", "elsewhere"])
    serve(monkeypatch, [{"id": 21, "from": 1, "text": "here", "out": True}])
    watch.round_once(Options(chat="777"), since=20)
    assert capsys.readouterr().out.splitlines()[-1] == "21 self here"


def test_a_refused_send_is_not_remembered(monkeypatch, tmp_path, capsys):
    log = tmp_path / "sent"
    monkeypatch.setenv("TG_SENT_LOG", str(log))
    monkeypatch.setattr(
        tg, "ask", lambda socket, request, deadline=None: {"ok": False, "error": "no"}
    )
    tg.main(["send", "--chat", "777", "--text", "x"])
    assert not log.exists() or log.read_text() == ""


def test_reading_is_not_remembered_as_sending(monkeypatch, tmp_path, capsys):
    log = tmp_path / "sent"
    monkeypatch.setenv("TG_SENT_LOG", str(log))
    serve(monkeypatch, [{"id": 5, "from": 1, "text": "x", "out": True}])
    tg.main(["read", "--chat", "777"])
    assert not log.exists() or log.read_text() == ""


def test_a_mark_is_kept_between_runs(monkeypatch, tmp_path, capsys):
    state = tmp_path / "mark"
    asked = serve(monkeypatch, [{"id": 30, "from": 42, "text": "hi", "out": False}])
    # The first run has nothing to resume from, so it starts at the newest message and
    # reports nothing older
    assert watch.main(["--chat", "777", "--once", "--state", str(state)]) == 0
    assert capsys.readouterr().out == ""
    assert state.read_text().strip() == "30"

    asked.clear()
    serve(monkeypatch, [{"id": 31, "from": 42, "text": "new", "out": False}])
    assert watch.main(["--chat", "777", "--once", "--state", str(state)]) == 0
    assert capsys.readouterr().out.splitlines() == ["31 42 new"]
    assert state.read_text().strip() == "31"


def test_a_command_runs_once_per_new_message_with_the_message_on_stdin(
    monkeypatch, tmp_path, capsys
):
    got = tmp_path / "got"
    serve(
        monkeypatch,
        [
            {"id": 41, "from": 42, "text": "one", "out": False},
            {"id": 42, "from": 42, "text": "two", "out": False},
        ],
    )
    watch.round_once(Options(exec=f"cat >> {got}; echo >> {got}"), since=40)
    events = [json.loads(line) for line in got.read_text().splitlines() if line]
    assert [(e["id"], e["text"]) for e in events] == [(41, "one"), (42, "two")]
    assert events[0]["chat"] == "777"


def test_a_failing_command_does_not_end_the_watch(monkeypatch, capsys):
    serve(monkeypatch, [{"id": 51, "from": 42, "text": "x", "out": False}])
    mark = watch.round_once(Options(exec="exit 3"), since=50)
    assert mark == 51
    assert "51 42 x" in capsys.readouterr().out
