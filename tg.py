#!/usr/bin/env python3
"""Talk to the tg-agentd socket. One request per call, one answer printed as JSON.

This half holds no secret and makes no decision. It sends what it is told and prints what
comes back, so the standard library is all it needs and it runs under whatever python is
at hand. Everything that decides lives behind the socket. Talking to the socket is
agentsock.py beside this file.
"""

import argparse
import base64
import json
import os
import sys

from agentsock import SentLog, ask, stream  # noqa: F401  — stream is for tg-watch.py

DEFAULT_SOCKET = os.environ.get("TG_SOCKET", "/run/tg-agent/socket")

# Messages this client sent, so a watcher can tell the agent's own sends from what the
# user sent from a phone: the server marks both as the account's
SENT = SentLog("TG_SENT_LOG", "tg-agent")


def remember_sent(chat, message_id):
    SENT.remember(chat, message_id)


def sent_ids(chat):
    """The identifiers this client sent into one chat, as it was named then."""
    return {int(identifier) for identifier in SENT.ids(chat)}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg.py",
        description="Send one request to the Telegram service and print its answer",
        epilog=(
            "The service decides. A refusal names the permission line that would lift "
            "it; take that line to the user rather than working around it.\n\n"
            "Examples:\n"
            "  tg.py permissions\n"
            "  tg.py whoami\n"
            "  tg.py chat-info --chat 777\n"
            "  tg.py read --chat -1001234567890 --limit 20\n"
            "  tg.py read --chat 777 --ids 12\n"
            "  tg.py react --chat 777 --id 12 --reactions '👍'\n"
            "  tg.py react --chat 777 --id 12 --reactions\n"
            "  tg.py send --chat 777 --text 'on my way'\n"
            "  tg.py send --chat 777 --text '**done**, see `out.csv`'\n"
            "  tg.py send --chat 777 --text '| a | b |' --parse-mode rich\n"
            "  tg.py send --chat 777 --file report.pdf --text 'the report'\n"
            "  tg.py digest --folder 5\n"
            "  tg.py mark-read --folder 5\n"
            "  tg.py dialogs\n"
            "  tg.py media --chat 777 --ids 12 13\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        help="the action to ask for; 'permissions' lists what is held, 'whoami' describes "
        "the account, 'chat-info' the other side of a chat under its read grant; "
        "'read' includes reaction counts and choices, 'react' sets reactions under write",
    )
    parser.add_argument(
        "--chat",
        help="the chat identifier the action applies to; 'saved' is the account's saved messages",
    )
    parser.add_argument("--folder", help="the folder identifier the action applies to")
    parser.add_argument("--to", help="the destination chat, for forward; 'saved' works here too")
    parser.add_argument(
        "--text", help="the message text, for send, reply and edit; with --file, its caption"
    )
    parser.add_argument(
        "--parse-mode",
        help="how --text is marked up: md (the service's default, Telethon's Markdown), "
        "html, none, or rich (Telegram's own Markdown with headings, tables and $math$, "
        "parsed by the server; not for a file's caption)",
    )
    parser.add_argument(
        "--file",
        help="a file to send with send or reply; this client reads it and the service never opens a path",
    )
    parser.add_argument("--reply-to", type=int, help="the message this one answers")
    parser.add_argument("--id", type=int, help="one message identifier, for edit or react")
    parser.add_argument("--ids", type=int, nargs="+", help="message identifiers")
    parser.add_argument(
        "--reactions", nargs="*",
        help="emoji reactions for react, replacing the account's choices; no values removes them",
    )
    parser.add_argument("--add", type=int, nargs="+", help="chats to add, for folder-edit")
    parser.add_argument("--remove", type=int, nargs="+", help="chats to remove, for folder-edit")
    parser.add_argument("--limit", type=int, help="how many messages to read")
    parser.add_argument("--topic", type=int, help="read one topic of a forum")
    parser.add_argument(
        "--since",
        type=int,
        help="only messages newer than this one, which is how a watcher asks for what it has not seen",
    )
    parser.add_argument(
        "--search",
        help="ask the server for messages holding this text, instead of reading history",
    )
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help="the service socket (default: $TG_SOCKET, else %(default)s)",
    )
    return parser


def request_from(options):
    """Everything the user named, and nothing they did not."""
    named = {
        "action": options.action,
        "chat": options.chat,
        "folder": options.folder,
        "to": options.to,
        "text": options.text,
        "parse_mode": options.parse_mode,
        "reply_to": options.reply_to,
        "id": options.id,
        "ids": options.ids,
        "reactions": options.reactions,
        "add": options.add,
        "remove": options.remove,
        "limit": options.limit,
        "search": options.search,
        "topic": options.topic,
        "since": options.since,
    }
    request = {key: value for key, value in named.items() if value is not None}
    if options.file:
        with open(options.file, "rb") as attached:
            data = attached.read()
        request["file"] = {
            "name": os.path.basename(options.file),
            "data": base64.b64encode(data).decode(),
        }
    return request


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        answer = ask(options.socket, request_from(options))
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"tg.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    if options.action in ("send", "reply"):
        # Only an answer that sent something carries the message it made
        sent = (answer.get("result") or {}).get("id")
        if sent is not None:
            remember_sent(options.chat, sent)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
