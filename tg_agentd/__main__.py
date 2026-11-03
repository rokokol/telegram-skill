"""Start the service: read the credentials, connect, and serve the socket."""

# A session that is not signed in is a configuration fault, not a failure to retry: the
# login is interactive, so restarting cannot fix it. The unit stops on this code instead
# of waking every few seconds for as long as the machine is up
EX_CONFIG = 78

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from . import handler, media, permissions, server, verbs


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
    parser.add_argument(
        "--login",
        action="store_true",
        help="sign in interactively instead of serving, and exit",
    )
    parser.add_argument(
        "--qr",
        action="store_true",
        help="with --login, sign in by a QR code scanned on a signed-in device",
    )
    return parser


def _vocabulary():
    """The permission words, printed from the tables that decide them."""
    lines = ["chat permissions:"]
    lines += [f"  {name:<16}{text}" for name, text in verbs.CHAT_ACTIONS.items()]
    lines += ["", "account permissions (chats/default.conf alone):"]
    lines += [f"  {name:<16}{text}" for name, text in verbs.ACCOUNT_ACTIONS.items()]
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


# Where Telegram says it put the code. The names are the constructors of
# auth.SentCodeType; a constructor this table does not know is printed as it is, because a
# channel invented here would be worse than a name nobody recognises
DELIVERY = {
    "SentCodeTypeApp": "through the Telegram app on a signed-in device: open the chat named Telegram",
    "SentCodeTypeSms": "by SMS",
    "SentCodeTypeCall": "by a phone call that reads the code aloud",
    "SentCodeTypeFlashCall": "by a flash call, whose number is the code",
    "SentCodeTypeMissedCall": "by a missed call, whose last digits are the code",
    "SentCodeTypeEmailCode": "by the login email of the account",
    "SentCodeTypeFragmentSms": "through fragment.com",
    "SentCodeTypeFirebaseSms": "by SMS after the app's attestation",
    "SentCodeTypeSmsWord": "by SMS as a secret word",
    "SentCodeTypeSmsPhrase": "by SMS as a secret phrase",
}


def describe_delivery(sent):
    """Say where Telegram sent the code, since it decides that and not this program."""
    channel = DELIVERY.get(type(sent.type).__name__, type(sent.type).__name__)
    length = getattr(sent.type, "length", None)
    if length:
        return f"tg-agentd: the code went {channel} ({length} digits)"
    return f"tg-agentd: the code went {channel}"


def show_qr(url):
    """Print the login link as a QR code a Telegram app can scan."""
    import qrcode

    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(invert=True)


async def code_login(telethon):
    """Ask for a phone and a code, and report the channel Telegram chose."""
    from telethon import errors

    phone = input("Please enter your phone (or bot token): ").strip()
    try:
        sent = await telethon.send_code_request(phone)
    except errors.FloodWaitError as wait:
        raise SystemExit(
            f"tg-agentd: Telegram asks to wait {wait.seconds} seconds before it sends another code"
        ) from None
    except errors.RPCError as refused:
        raise SystemExit(
            f"tg-agentd: Telegram refused the code request: {type(refused).__name__}: {refused}"
        ) from None
    print(describe_delivery(sent))
    code = input("Please enter the code you received: ").strip()
    try:
        return await telethon.sign_in(phone, code)
    except errors.SessionPasswordNeededError:
        raise
    except errors.FloodWaitError as wait:
        raise SystemExit(
            f"tg-agentd: Telegram asks to wait {wait.seconds} seconds before another attempt"
        ) from None
    except errors.RPCError as refused:
        raise SystemExit(
            f"tg-agentd: Telegram refused the code: {type(refused).__name__}: {refused}"
        ) from None


async def qr_login(telethon):
    """Show a QR code, replacing it when the token expires, until a device accepts it."""
    qr = await telethon.qr_login()
    while True:
        print("tg-agentd: scan this with Telegram, or open the link on a signed-in device:")
        print(qr.url)
        show_qr(qr.url)
        try:
            return await qr.wait()
        except asyncio.TimeoutError:
            print("tg-agentd: the QR code expired; here is a new one")
            await qr.recreate()


async def login(telethon, *, by_qr):
    """Sign the client in, by a code or by a QR code, with the password when 2FA asks."""
    from telethon import errors

    await telethon.connect()
    if await telethon.is_user_authorized():
        return
    try:
        if by_qr:
            return await qr_login(telethon)
        return await code_login(telethon)
    except errors.SessionPasswordNeededError:
        password = getpass.getpass("Please enter your password: ")
        try:
            return await telethon.sign_in(password=password)
        except errors.RPCError as refused:
            raise SystemExit(
                f"tg-agentd: Telegram refused the password: {type(refused).__name__}: {refused}"
            ) from None


def build_handler(options, account):
    """The handler the service serves, assembled from its command line."""
    store = permissions.Store(options.permissions)
    outbox = media.Outbox(options.media_dir) if options.media_dir else None
    return handler.Handler(store, account, outbox=outbox)


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
        # Its own reconnect retries a throttling answer with no pause and then stops for
        # good; the watch loop starts below owns reconnecting instead
        auto_reconnect=False,
    )
    if options.login:
        # The session has to be written by the same identity that will read it, in the
        # same state directory, so the login runs as this service rather than beside it
        await login(telethon, by_qr=options.qr)
        me = await telethon.get_me()
        print(f"tg-agentd: signed in as {getattr(me, 'username', None) or me.id}")
        await telethon.disconnect()
        return

    account = Client(telethon)
    await account.start()
    if not await telethon.is_user_authorized():
        print(
            "tg-agentd: the session is not signed in — run the login once by hand "
            f"against {state}, as references/setup.md describes",
            file=sys.stderr,
        )
        raise SystemExit(EX_CONFIG)

    served = server.Server(build_handler(options, account))
    inherited = server.inherited_socket()
    if inherited is None and not options.socket:
        raise SystemExit("tg-agentd: no socket was passed and none was named")
    watching = asyncio.create_task(account.watch())
    serving = asyncio.create_task(
        served.serve_forever(sock=inherited)
        if inherited is not None
        else served.serve_forever(path=str(options.socket))
    )
    try:
        done, _ = await asyncio.wait(
            {serving, watching}, return_when=asyncio.FIRST_COMPLETED
        )
        if watching in done and not serving.done():
            print(
                "tg-agentd: the link to Telegram stopped answering; exiting so that "
                "systemd restarts the service",
                file=sys.stderr,
            )
            raise SystemExit(os.EX_TEMPFAIL)
        serving.result()
    finally:
        for task in (watching, serving):
            task.cancel()
        await asyncio.gather(watching, serving, return_exceptions=True)
        await account.stop()


def main(argv=None):
    parser = build_parser()
    options = parser.parse_args(argv)
    if options.qr and not options.login:
        parser.error("--qr needs --login")
    try:
        asyncio.run(run(options))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
