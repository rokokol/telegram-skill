#!/usr/bin/env python3
"""Talk to the tg-agentd socket. One request per call, one answer printed as JSON.

This half holds no secret and makes no decision. It sends what it is told and prints what
comes back, so the standard library is all it needs and it runs under whatever python is
at hand. Everything that decides lives behind the socket.
"""

import argparse
import json
import os
import socket
import sys

DEFAULT_SOCKET = os.environ.get("TG_SOCKET", "/run/tg-agent/socket")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg.py",
        description="Send one request to the Telegram service and print its answer",
        epilog=(
            "The service decides. A refusal names the permission line that would lift "
            "it; take that line to the user rather than working around it.\n\n"
            "Examples:\n"
            "  tg.py permissions\n"
            "  tg.py read --chat -1001234567890 --limit 20\n"
            "  tg.py send --chat 777 --text 'on my way'\n"
            "  tg.py digest --folder 5\n"
            "  tg.py media --chat 777 --ids 12 13\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", help="the action to ask for; 'permissions' lists what is held")
    parser.add_argument("--chat", help="the chat identifier the action applies to")
    parser.add_argument("--folder", help="the folder identifier the action applies to")
    parser.add_argument("--to", help="the destination chat, for forward")
    parser.add_argument("--text", help="the message text, for send, reply and edit")
    parser.add_argument("--reply-to", type=int, help="the message this one answers")
    parser.add_argument("--id", type=int, help="one message identifier, for edit")
    parser.add_argument("--ids", type=int, nargs="+", help="message identifiers")
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
        "reply_to": options.reply_to,
        "id": options.id,
        "ids": options.ids,
        "add": options.add,
        "remove": options.remove,
        "limit": options.limit,
        "search": options.search,
        "topic": options.topic,
        "since": options.since,
    }
    return {key: value for key, value in named.items() if value is not None}


def ask(path, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        answer = b""
        while not answer.endswith(b"\n"):
            block = connection.recv(65536)
            if not block:
                break
            answer += block
    return json.loads(answer.decode())


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        answer = ask(options.socket, request_from(options))
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"tg.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
