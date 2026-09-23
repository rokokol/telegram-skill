#!/usr/bin/env python3
"""Follow a chat and print each new message as one line.

Every line is an event: the identifier, who sent it, and the text with newlines folded
away, so a reader that consumes lines sees one message per line. The identifier of the
last message seen is what the next request asks from, rather than a count of recent ones
— asking for the last N loses whatever arrived between two rounds and repeats what did
not.

Both sides are followed. A message the account itself sent from a phone is as much an
event as one that arrived, and a watcher that hides it shows half a conversation.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tg  # noqa: E402  — the client beside this file, which owns talking to the socket

DEFAULT_INTERVAL = 20


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg-watch.py",
        description="Print each new message in a chat as it arrives, one line each",
        epilog=(
            "The chat needs read permission, like any other reading.\n\n"
            "Examples:\n"
            "  tg-watch.py --chat 932504634\n"
            "  tg-watch.py --chat -1001804413056 --topic 43628 --interval 60\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--chat", required=True, help="the chat to follow")
    parser.add_argument("--topic", type=int, help="one topic of a forum, if it is one")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="seconds between rounds (default: %(default)s)",
    )
    parser.add_argument(
        "--since",
        type=int,
        help="start from this message instead of from the latest one",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print what is new and exit, instead of following",
    )
    parser.add_argument(
        "--socket",
        default=tg.DEFAULT_SOCKET,
        help="the service socket (default: $TG_SOCKET, else %(default)s)",
    )
    return parser


def line_for(message):
    """One event as one line: nothing in it may contain a newline."""
    who = "self" if message.get("out") else str(message.get("from"))
    text = (message.get("text") or "[attachment]").replace("\n", " ")
    return f"{message['id']} {who} {text}"


def newest(messages):
    """The highest identifier in a batch, or None when it is empty."""
    return max((m["id"] for m in messages), default=None)


def request_for(options, since):
    request = {"action": "read", "chat": options.chat, "limit": 50}
    if since is not None:
        request["since"] = since
    if options.topic:
        request["topic"] = options.topic
    return request


def latest_id(options):
    """Where to start when the caller named no starting point."""
    answer = tg.ask(options.socket, request_for(options, None) | {"limit": 1})
    if not answer.get("ok"):
        raise SystemExit(f"tg-watch.py: {answer.get('error')}")
    return newest(answer.get("result") or [])


def round_once(options, since):
    """Print what is newer than `since`, and answer with the new starting point.

    A refusal is printed and the starting point kept: a permission withdrawn while this
    runs should stop the output, not the watch, because the user may put it back.
    """
    answer = tg.ask(options.socket, request_for(options, since))
    if not answer.get("ok"):
        print(f"refused: {answer.get('error')}", flush=True)
        return since
    messages = sorted(answer.get("result") or [], key=lambda m: m["id"])
    for message in messages:
        print(line_for(message), flush=True)
    return newest(messages) or since


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        since = options.since if options.since is not None else latest_id(options)
        while True:
            since = round_once(options, since)
            if options.once:
                return 0
            time.sleep(options.interval)
    except KeyboardInterrupt:
        return 0
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"tg-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
