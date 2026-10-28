"""The units in systemd/ keep the isolation the NixOS module gives.

A system without Nix runs the service from these files, so each property the module
promises is read back from them. A unit that loses one still starts and still answers,
which is why nothing but a test would notice.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS = ROOT / "systemd"
MODULE = (ROOT / "nix" / "module.nix").read_text()


def directives(name):
    """Every key=value line of a unit, a key that repeats keeping all its values."""
    found = {}
    for line in (UNITS / name).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        found.setdefault(key.strip(), []).append(value.strip())
    return found


SERVICE = directives("tg-agent.service")
SOCKET = directives("tg-agent.socket")


def test_the_service_runs_under_a_dynamic_user_with_its_state_private():
    assert SERVICE["DynamicUser"] == ["yes"]
    assert SERVICE["StateDirectory"] == ["tg-agent"]


def test_both_credentials_reach_the_service_through_systemd_alone():
    loaded = sorted(value.split(":", 1)[0] for value in SERVICE["LoadCredential"])
    assert loaded == ["api_hash", "api_id"]
    assert all("/etc/tg-agent/" in value for value in SERVICE["LoadCredential"])
    assert "api_" not in SERVICE["ExecStart"][0]


def test_the_sandbox_matches_the_module():
    for key in (
        "PrivateMounts",
        "PrivateDevices",
        "ProtectHome",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectControlGroups",
        "NoNewPrivileges",
    ):
        assert SERVICE[key] == ["yes"], key
        assert re.search(rf"\b{key} = true;", MODULE), key
    assert SERVICE["ProtectSystem"] == ["strict"]
    assert SERVICE["RestrictAddressFamilies"] == ["AF_UNIX AF_INET AF_INET6"]


def test_a_session_that_is_not_signed_in_is_not_restarted():
    assert "78" in SERVICE["RestartPreventExitStatus"][0].split()
    assert 'RestartPreventExitStatus = "78";' in MODULE


def test_missing_credentials_are_not_restarted():
    # 243 is systemd's EXIT_CREDENTIALS: the pair is absent, and only the user can add it
    assert "243" in SERVICE["RestartPreventExitStatus"][0].split()


def test_permissions_and_outbox_lie_outside_the_state_directory():
    command = SERVICE["ExecStart"][0]
    permissions = re.search(r"--permissions (\S+)", command).group(1)
    outbox = re.search(r"--media-dir (\S+)", command).group(1)
    for path in (permissions, outbox):
        assert not f"{path}/".startswith("/var/lib/tg-agent/"), path
    assert SERVICE["ReadWritePaths"] == [outbox]


def test_the_socket_is_closed_to_everyone_but_its_owner():
    assert SOCKET["SocketMode"] == ["0600"]
    assert SOCKET["ListenStream"] == ["/run/tg-agent/socket"]


def test_the_login_writes_where_the_service_reads():
    login = (UNITS / "tg-agent-login").read_text()
    assert "--property=DynamicUser=yes" in login
    assert "--property=StateDirectory=tg-agent" in login
    assert "/var/lib/tg-agent-permissions" in SERVICE["ExecStart"][0]
    assert "--permissions /var/lib/tg-agent-permissions" in login
