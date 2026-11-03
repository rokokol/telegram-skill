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


def test_an_unreachable_socket_is_still_a_different_answer(tmp_path, capsys):
    status = tg.main(["whoami", "--socket", str(tmp_path / "nothing")])
    assert status == 2
    assert "systemctl restart" not in capsys.readouterr().err
