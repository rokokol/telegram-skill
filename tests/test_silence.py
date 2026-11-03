"""A service that takes the connection and answers nothing is a fault of its own.

A hung tg-agentd accepts every connection and then never writes an answer, so without a
deadline of its own the caller waits out somebody else's timeout and reports a slow tool
rather than a stuck service. Every call here is bounded by the test, not by the service:
a check that hangs is worth less than one that fails and says what never came back.
"""

import socket
import threading

import pytest

import agentsock
import tg


def silent_service(path):
    """A socket that accepts one connection and answers nothing on it."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    accepted = []

    def accept():
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        accepted.append(connection)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    return listener, accepted


def within(seconds, call):
    """Answer with what `call` answered, or fail saying it never came back."""
    box = {"value": None, "failed": None}

    def run():
        try:
            box["value"] = call()
        except BaseException as failure:  # the caller decides what one means
            box["failed"] = failure

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(
            f"the call had no deadline of its own: nothing came back in {seconds}s"
        )
    if box["failed"] is not None:
        raise box["failed"]
    return box["value"]


def test_an_answer_that_never_comes_ends_at_the_deadline(tmp_path, monkeypatch):
    path = str(tmp_path / "socket")
    listener, accepted = silent_service(path)
    monkeypatch.setenv("TG_TIMEOUT", "0.2")
    try:
        with pytest.raises(TimeoutError):
            within(5, lambda: agentsock.ask(path, {"action": "whoami"}))
    finally:
        for connection in accepted:
            connection.close()
        listener.close()


# The deadline is read from the environment so a caller can shorten it for one long read
# without a flag, and a value that is not a positive number falls back to the default
def test_the_deadline_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("TG_TIMEOUT", "2.5")
    assert agentsock.deadline_from() == 2.5
    monkeypatch.setenv("TG_TIMEOUT", "-1")
    assert agentsock.deadline_from() == agentsock.DEFAULT_DEADLINE
    monkeypatch.setenv("TG_TIMEOUT", "soon")
    assert agentsock.deadline_from() == agentsock.DEFAULT_DEADLINE
    monkeypatch.delenv("TG_TIMEOUT")
    assert agentsock.deadline_from() == agentsock.DEFAULT_DEADLINE


# A caller that hears nothing has to learn what to do about it: the service, not the
# request, is the fault, and restarting it is the way out
def test_a_silent_service_is_reported_with_the_line_that_restarts_it(
    tmp_path, monkeypatch, capsys
):
    path = str(tmp_path / "socket")
    listener, accepted = silent_service(path)
    monkeypatch.setenv("TG_TIMEOUT", "0.2")
    try:
        status = within(5, lambda: tg.main(["whoami", "--socket", path]))
    finally:
        for connection in accepted:
            connection.close()
        listener.close()
    printed = capsys.readouterr().err
    assert status == 3
    assert "tg-agent.service" in printed
    assert "systemctl restart" in printed
    assert "Traceback" not in printed


def scripted_service(path, answers):
    """A socket that answers its connections in order: a line each, or None for silence."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(4)
    accepted = []

    def serve():
        for answer in answers:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            accepted.append(connection)
            if answer is not None:
                connection.sendall(answer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener, accepted


PING_TELEGRAM_UP = (
    b'{"ok": true, "action": "ping", "result": {"telegram": true}}\n'
)
PING_TELEGRAM_DOWN = (
    b'{"ok": true, "action": "ping", "result": {"telegram": false}}\n'
)


# A heavy action is given longer than a light one, so a digest of a whole folder is not
# cut at the deadline that suits a single chat
def test_an_action_that_reads_a_whole_folder_is_given_longer(monkeypatch):
    monkeypatch.delenv("TG_TIMEOUT", raising=False)
    assert tg.patience("digest") > tg.patience("read")
    assert tg.patience("read") == tg.DEFAULT_PATIENCE
    monkeypatch.setenv("TG_TIMEOUT", "7")
    assert tg.patience("digest") == 7, "the caller's own ceiling was overruled"


def test_the_patience_reaches_the_call(monkeypatch):
    seen = []

    def ask(socket, request, deadline=None):
        seen.append((request["action"], deadline))
        return {"ok": True, "result": []}

    monkeypatch.delenv("TG_TIMEOUT", raising=False)
    monkeypatch.setattr(tg, "ask", ask)
    assert tg.main(["digest", "--folder", "5"]) == 0
    assert tg.main(["whoami"]) == 0
    assert seen == [("digest", tg.PATIENCE["digest"]), ("whoami", tg.DEFAULT_PATIENCE)]


# A long action is not a stuck service, and the client asks before it accuses one
def test_a_long_action_is_not_reported_as_a_stuck_service(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "socket")
    listener, accepted = scripted_service(path, [None, PING_TELEGRAM_UP])
    monkeypatch.setenv("TG_TIMEOUT", "0.2")
    try:
        status = within(10, lambda: tg.main(["digest", "--folder", "5", "--socket", path]))
    finally:
        for connection in accepted:
            connection.close()
        listener.close()
    printed = capsys.readouterr().err
    assert status == 3
    assert "still working" in printed
    assert "TG_TIMEOUT" in printed
    assert "systemctl restart" not in printed


def test_a_link_that_is_down_is_not_reported_as_a_stuck_service(
    tmp_path, monkeypatch, capsys
):
    path = str(tmp_path / "socket")
    listener, accepted = scripted_service(path, [None, PING_TELEGRAM_DOWN])
    monkeypatch.setenv("TG_TIMEOUT", "0.2")
    try:
        status = within(10, lambda: tg.main(["read", "--chat", "777", "--socket", path]))
    finally:
        for connection in accepted:
            connection.close()
        listener.close()
    printed = capsys.readouterr().err
    assert status == 3
    assert "link to Telegram is down" in printed
    assert "systemctl restart" not in printed


def test_a_write_is_not_repeated_blindly(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "socket")
    listener, accepted = scripted_service(path, [None, PING_TELEGRAM_UP])
    monkeypatch.setenv("TG_TIMEOUT", "0.2")
    try:
        within(
            10,
            lambda: tg.main(
                ["send", "--chat", "777", "--text", "hi", "--socket", path]
            ),
        )
    finally:
        for connection in accepted:
            connection.close()
        listener.close()
    printed = capsys.readouterr().err
    assert "may have landed" in printed


def test_an_unreachable_socket_is_still_a_different_answer(tmp_path, capsys):
    status = tg.main(["whoami", "--socket", str(tmp_path / "nothing")])
    assert status == 2
    assert "systemctl restart" not in capsys.readouterr().err
