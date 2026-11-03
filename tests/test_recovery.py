"""The service survives a lost link to Telegram, and a caller is told what happened.

Telethon's own reconnection is built for a chat app: it retries a throttling answer with no
pause at all, gives up after a handful of attempts, and leaves the client disconnected for
good. This service turns that off and supervises the link itself, so the tests here pin the
supervision: a refusal a caller can read, a pause that grows between attempts, and a
non-zero exit once the outage outlives the budget, which is what asks systemd for a restart
"""

import asyncio
import json
import socket
import time
from pathlib import Path

import pytest
import telethon

from tests.fake_telethon import FakeClient
from tg_agentd import __main__ as entry
from tg_agentd import client as client_module
from tg_agentd import server


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


class _Gone:
    """A handler whose Telegram side is unreachable."""

    async def handle(self, request):
        raise ConnectionError("Cannot send requests while disconnected")

    async def stream(self, request):
        raise ConnectionError("Cannot send requests while disconnected")
        yield


def _once(tmp_path, payload):
    """Send one line to a real Server and read one answer line."""

    path = str(tmp_path / "socket")

    async def scenario():
        served = asyncio.ensure_future(server.Server(_Gone()).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        answer = await asyncio.to_thread(_exchange, path, payload)
        served.cancel()
        return answer

    return asyncio.run(scenario())


def _exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        connection.sendall(payload)
        answer = b""
        while not answer.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                break
            answer += chunk
    return json.loads(answer)


def test_a_request_while_telegram_is_gone_is_refused_with_a_reason(tmp_path):
    answer = _once(tmp_path, b'{"action": "whoami"}\n')
    assert answer["ok"] is False
    assert "unreachable" in answer["error"]
    assert "disconnected" not in answer["error"], "the caller got Telethon's own wording"


def test_a_subscription_that_loses_telegram_gets_a_reason_and_ends(tmp_path):
    answer = _once(tmp_path, b'{"action": "subscribe", "chat": 777}\n')
    assert answer["ok"] is False
    assert "unreachable" in answer["error"]


class _Reconnecting(FakeClient):
    """A client whose link is down and whose reconnect attempts all fail."""

    def __init__(self):
        super().__init__()
        self.connected = True
        self.attempts = []

    async def connect(self):
        self._record("connect")
        if len(self.called("connect")) == 1:
            return
        self.attempts.append(time.monotonic())
        raise ConnectionError("refused")

    def is_connected(self):
        return False


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    return _Reconnecting()


def test_reconnecting_says_the_account_is_away_again(fake):
    account = client_module.Client(fake)
    asyncio.run(account.reconnect())
    statuses = [call for call in fake.called("raw") if call["request"] == "_FakeStatusRequest"]
    assert statuses and statuses[-1]["offline"] is True


def test_the_pause_between_reconnect_attempts_grows(fake, monkeypatch):
    monkeypatch.setattr(client_module, "WATCH_INTERVAL", 0.001)
    monkeypatch.setattr(client_module, "RETRY_PAUSE", 0.02)
    monkeypatch.setattr(client_module, "RETRY_CAP", 0.08)
    monkeypatch.setattr(client_module, "GIVE_UP_AFTER", 3)
    account = client_module.Client(fake)
    # The service starts once, and every connect after that is an attempt to come back
    asyncio.run(account.start())
    asyncio.run(asyncio.wait_for(account.watch(), 2))
    gaps = [round(b - a, 3) for a, b in zip(fake.attempts, fake.attempts[1:])]
    assert len(fake.attempts) == 3, fake.attempts
    assert gaps[1] > gaps[0] * 1.5, gaps
    assert gaps[0] > 0.005, "the second attempt came with no pause, as Telethon does it"


class _Stuck(FakeClient):
    """A session whose flag says connected while its transport is gone.

    This is what a failed reconnect left behind on 2026-10-30: the connection under the
    loops was nulled, the send loop died on the attribute error with nothing to await it,
    and `is_connected` still said yes because it answers from a flag. A plain `connect`
    cannot repair it, because Telethon refuses a sender that still calls itself connected
    """

    def __init__(self):
        super().__init__()
        self.deaf = True

    def is_connected(self):
        return True

    async def __call__(self, request):
        self._record("raw", request=type(request).__name__)
        if self.deaf:
            raise ConnectionError("the transport is gone")
        return None

    async def disconnect(self):
        self._record("disconnect")
        self.deaf = True

    async def connect(self):
        self._record("connect")
        if not self.called("disconnect"):
            # What Telethon answers a sender that still calls itself connected
            return False
        self.deaf = False


class _Deaf(_Stuck):
    """The same, except that no rebuild reaches Telegram either."""

    async def connect(self):
        self._record("connect")
        return True


def short_watch(monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    monkeypatch.setattr(client_module, "WATCH_INTERVAL", 0.001)
    monkeypatch.setattr(client_module, "PROBE_INTERVAL", 0.005)
    monkeypatch.setattr(client_module, "RETRY_PAUSE", 0.001)
    monkeypatch.setattr(client_module, "RETRY_CAP", 0.002)


def test_a_link_that_calls_itself_connected_is_rebuilt(monkeypatch):
    short_watch(monkeypatch)
    fake = _Stuck()
    account = client_module.Client(fake)
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(account.watch(), 0.3))
    assert fake.called("disconnect"), "the dead state was left standing"
    assert fake.called("connect")
    assert fake.deaf is False, "the link was never rebuilt, only reconnected"


def test_a_link_that_never_answers_leaves_the_restart_to_systemd(monkeypatch):
    short_watch(monkeypatch)
    monkeypatch.setattr(client_module, "GIVE_UP_AFTER", 3)
    fake = _Deaf()
    account = client_module.Client(fake)
    went = asyncio.run(asyncio.wait_for(account.watch(), 2))
    assert went is False, "a link that answered nothing kept the service alive"
    probes = [
        call for call in fake.called("raw") if call["request"] == "_FakeStatusRequest"
    ]
    assert len(probes) >= 3, probes
    assert len(fake.called("connect")) >= 3, "the link was never rebuilt at all"


def test_the_service_asks_systemd_for_a_restart_when_telegram_stays_gone(
    tmp_path, monkeypatch
):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "api_id").write_text("12345\n")
    (credentials / "api_hash").write_text("0" * 32 + "\n")
    state = tmp_path / "state"
    state.mkdir()
    permissions = tmp_path / "permissions"
    (permissions / "chats").mkdir(parents=True)
    (permissions / "folders").mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    monkeypatch.setenv("STATE_DIRECTORY", str(state))
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    monkeypatch.setattr(client_module, "WATCH_INTERVAL", 0.001)
    monkeypatch.setattr(client_module, "RETRY_PAUSE", 0.001)
    monkeypatch.setattr(client_module, "RETRY_CAP", 0.002)
    monkeypatch.setattr(client_module, "GIVE_UP_AFTER", 3)
    fake = _Reconnecting()
    built = {}

    def build(*args, **kwargs):
        built.update(kwargs)
        return fake

    monkeypatch.setattr(telethon, "TelegramClient", build)
    options = entry.build_parser().parse_args(
        [
            "--permissions",
            str(permissions),
            "--socket",
            str(tmp_path / "socket"),
        ]
    )
    with pytest.raises(SystemExit) as exit_code:
        asyncio.run(asyncio.wait_for(entry.run(options), 2))
    assert exit_code.value.code == 75, exit_code.value
    assert built.get("auto_reconnect") is False, (
        "Telethon's own unpaused reconnect is left on"
    )
    assert len(fake.attempts) == 3, fake.attempts
