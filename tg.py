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

# How long one action's answer may take, in seconds. One chat answers at once, a digest
# reads every member of a folder before it answers, and a download waits for a file, so a
# single deadline would cut the heavy actions while it left the light ones waiting. The
# table is this client's patience, not a claim about the service's speed, and $TG_TIMEOUT
# overrides every value in it
PATIENCE = {"digest": 900, "media": 300, "dialogs": 120, "find": 120}

# The deadline every action falls back to, and the variable that overrides all of them
DEFAULT_PATIENCE = 60
TIMEOUT_VARIABLE = "TG_TIMEOUT"

# The actions that write something, where a call that ended at its deadline may already
# have landed on the other side
WRITES = {"send", "reply", "forward", "edit", "react", "mark-read", "delete", "delete-for-all"}

# How long the question behind a silence may take. It is one request the service either
# makes or fails at once, so it needs no patience of the action's own
PING_DEADLINE = 10

# Messages this client sent, so a watcher can tell the agent's own sends from what the
# user sent from a phone: the server marks both as the account's
SENT = SentLog("TG_SENT_LOG", "tg-agent")


def timeout_from(environ=None):
    """The ceiling the caller named in $TG_TIMEOUT, or None when it named none.

    A value that is not a positive number names none: a caller shortening a long read and
    a caller who wrote the variable by mistake look the same here
    """
    environ = os.environ if environ is None else environ
    try:
        seconds = float((environ.get(TIMEOUT_VARIABLE) or "").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def patience(action):
    """How many seconds one action's answer may take before the client stops waiting."""
    return timeout_from() or PATIENCE.get(action, DEFAULT_PATIENCE)


def silence_report(socket_path, action):
    """What to tell a caller whose call ended at its deadline.

    Silence reads the same from here whether the service is grinding through a long
    action, has lost its link to Telegram while it runs, or has stopped answering at all.
    A ping is one cheap question that separates the three, and each answer has its own
    way out: repeating with more patience, waiting for the link, or a restart
    """
    try:
        answer = ask(
            socket_path,
            {"action": "ping"},
            deadline=min(PING_DEADLINE, patience(action)),
        )
    except (TimeoutError, OSError, json.JSONDecodeError):
        return [
            "tg-agent.service took the connection and answered nothing: restart it "
            "with systemctl restart tg-agent.service"
        ]
    if not answer.get("ok"):
        return [f"the service refused a ping: {answer.get('error')}"]
    if not (answer.get("result") or {}).get("telegram"):
        return [
            "the service is running, and its link to Telegram is down: it rebuilds "
            "that link itself, so repeat the call in a few minutes instead of "
            "restarting it"
        ]
    said = [
        f"the service is running and reached Telegram, so {action} is still working: "
        "set TG_TIMEOUT to more seconds and repeat the call"
    ]
    if action in WRITES:
        said.append(
            "check the chat before repeating a write: the first call may have landed"
        )
    return said


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
            "  tg.py topics --chat -1001234567890\n"
            "  tg.py read --chat -1001234567890 --topic 'Newcomers 1'\n"
            "  tg.py mark-read --chat -1001234567890 --topic 'Newcomers 1'\n"
            "  tg.py mark-read --chat 777 --id 120\n"
            "  tg.py find --name 'club'\n"
            "  tg.py find --name '@tech_club'\n"
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
            "\n"
            "A call waits $TG_TIMEOUT seconds for its answer: 60 by default, more for\n"
            "digest, media, dialogs and find, which read a whole folder or a file. A\n"
            "call still waiting at its deadline asks the service why, so a long action\n"
            "is not reported as a hung one. Exit codes: 0 allowed, 1 refused, 2 the\n"
            "socket could not be reached, 3 no answer in time\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        help="the action to ask for; 'permissions' lists what is held, 'whoami' describes "
        "the account, 'chat-info' the other side of a chat under its read grant; 'topics' "
        "lists a forum's topics with their numbers and unread counts, 'find' looks up the "
        "chats a grant names by their name; 'read' includes reaction counts and choices, "
        "'react' sets reactions under write",
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
    parser.add_argument(
        "--id",
        type=int,
        help="one message identifier, for edit and react; with mark-read, the message the "
        "read stops at, in a chat, a folder or a topic",
    )
    parser.add_argument("--ids", type=int, nargs="+", help="message identifiers")
    parser.add_argument(
        "--reactions", nargs="*",
        help="emoji reactions for react, replacing the account's choices; no values removes them",
    )
    parser.add_argument("--add", type=int, nargs="+", help="chats to add, for folder-edit")
    parser.add_argument("--remove", type=int, nargs="+", help="chats to remove, for folder-edit")
    parser.add_argument("--limit", type=int, help="how many messages to read")
    parser.add_argument(
        "--topic",
        help="read or mark read one topic of a forum, by its number or by its title",
    )
    parser.add_argument(
        "--since",
        type=int,
        help="only messages newer than this one, which is how a watcher asks for what it has not seen",
    )
    parser.add_argument(
        "--name",
        help="with find, the text a chat's name or username holds, matched without case",
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
        "name": options.name,
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
        # The patience is this file's, so an action that reads a whole folder may take
        # longer than one that reads a single chat; the deadline inside agentsock stays
        # the default for every other caller
        answer = ask(
            options.socket, request_from(options), deadline=patience(options.action)
        )
    except TimeoutError as silent:
        print(f"tg.py: {silent}", file=sys.stderr)
        for line in silence_report(options.socket, options.action):
            print(line, file=sys.stderr)
        return 3
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
