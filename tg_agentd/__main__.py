"""Start the service: read the credentials, connect, and serve the socket."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import handler, permissions, server, verbs


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg-agentd",
        description="Serve a Telegram account over a unix socket, under per-chat permissions",
        epilog=_vocabulary(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--permissions",
        required=True,
        type=Path,
        help="the directory holding chats/ and folders/, which this service only reads",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        help="bind this path; omit it when systemd passes the socket instead",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        help="where downloaded files land; downloads are refused without it",
    )
    parser.add_argument(
        "--device",
        default="tg-agentd",
        help="the device name this session shows in the account's device list",
    )
    return parser


def _vocabulary():
    """The permission words, printed from the tables that decide them."""
    lines = ["chat permissions:"]
    lines += [f"  {name:<16}{text}" for name, text in verbs.CHAT_ACTIONS.items()]
    lines += ["", "folder permissions:"]
    lines += [f"  {name:<16}{text}" for name, text in verbs.FOLDER_ACTIONS.items()]
    lines += [
        "",
        f"  {verbs.WILDCARD:<16}every action in the table above, for a target with no",
        f"  {'':<16}other party in it; every answer says when it was used",
    ]
    return "\n".join(lines)


def credentials():
    """Read the api pair from the credentials directory systemd provides.

    The pair never reaches an argument or the environment of a child process: the
    directory is readable only inside this unit's namespace.
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise SystemExit("tg-agentd: no CREDENTIALS_DIRECTORY, so there is no api pair")
    root = Path(directory)
    try:
        api_id = int((root / "api_id").read_text().strip())
        api_hash = (root / "api_hash").read_text().strip()
    except (FileNotFoundError, ValueError) as missing:
        raise SystemExit(f"tg-agentd: the credentials are unusable: {missing}") from None
    return api_id, api_hash


async def run(options):
    # Imported here so that --help works without the library installed
    from telethon import TelegramClient

    from .client import Client

    api_id, api_hash = credentials()
    state = Path(os.environ.get("STATE_DIRECTORY", "."))
    telethon = TelegramClient(
        str(state / "account.session"),
        api_id,
        api_hash,
        device_model=options.device,
    )
    account = Client(telethon)
    await account.start()
    if not await telethon.is_user_authorized():
        raise SystemExit("tg-agentd: the session is not signed in")

    store = permissions.Store(options.permissions)
    served = server.Server(handler.Handler(store, account))
    inherited = server.inherited_socket()
    try:
        if inherited is not None:
            await served.serve_forever(sock=inherited)
        elif options.socket:
            await served.serve_forever(path=str(options.socket))
        else:
            raise SystemExit("tg-agentd: no socket was passed and none was named")
    finally:
        await account.stop()


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
