<div align="center">

# Telegram skill

**Your account, reachable by an agent that cannot widen its own reach (=^･ω･^=)**

[![Agent Skill](https://img.shields.io/badge/Agent_Skill-6E56CF?style=flat)](https://agentskills.io)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Telethon](https://img.shields.io/badge/Telethon-2CA5E0?style=flat&logo=telegram&logoColor=white)
![systemd](https://img.shields.io/badge/systemd-30D475?style=flat&logo=systemd&logoColor=white)
![Nix](https://img.shields.io/badge/Nix-5277C3?style=flat&logo=nixos&logoColor=white)
[![license](https://img.shields.io/badge/MIT-3DA639?style=flat)](LICENSE)
[![ci](https://github.com/rokokol/telegram-skill/actions/workflows/build.yml/badge.svg)](https://github.com/rokokol/telegram-skill/actions/workflows/build.yml)
[![falsify](https://github.com/rokokol/telegram-skill/actions/workflows/falsify.yml/badge.svg)](https://github.com/rokokol/telegram-skill/actions/workflows/falsify.yml)

</div>

Lets an agent read and write your Telegram chats over MTProto, under permissions you write and it cannot. The session lives inside a systemd unit's namespace, the agent talks to a unix socket, and the vocabulary the socket accepts has no word for deleting the account

An agent that reads your chats and writes to them shares one channel with everyone who messages you, so a line in a group chat arrives looking exactly like an instruction from you. This repository answers that where it can be answered: the secret is somewhere the agent cannot read, the permission files are somewhere it cannot write, and every action it takes is a word you granted on a chat you named

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [How it is arranged](#how-it-is-arranged)
- [Permissions](#permissions)
- [What it does not do](#what-it-does-not-do)
- [Tests](#tests)

## Requirements

Linux with systemd, an `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org), and a Telegram account you are willing to give a second session to

There is no macOS support and there will not be: the secret's isolation is a systemd unit's mount namespace, and a platform without systemd has nowhere to put it

## Install

```bash
npx skills add -g rokokol/telegram-skill    # for you, everywhere
npx skills add rokokol/telegram-skill       # for the project you are standing in
```

The service half comes with its own NixOS module, so a system configuration takes this repository as a flake input and turns it on:

```nix
services.tg-agent = {
  enable = true;
  apiIdFile = "/run/secrets/telegram-api-id";
  apiHashFile = "/run/secrets/telegram-api-hash";
  socketUser = "you";
};
```

The module is where the isolation lives — `DynamicUser`, credentials read before the service drops its privileges, the session under `/var/lib/private` — so it ships here rather than being written again by everyone who runs it. `references/setup.md` says what it provides and why

## How it is arranged

```
you ──► agent ──► tg.py ──► unix socket ──► tg-agentd ──► Telegram
                                               │
                                               ├── credentials, in the unit's namespace
                                               ├── session, under /var/lib/private
                                               └── permission files, read-only
```

The client holds no secret and makes no decision. It sends a request and prints the answer, so the standard library is all it needs. Everything that decides is behind the socket, where the agent cannot reach it

Three properties are tests rather than promises, and each has a planted defect proving the suite would notice: no path that reads history marks it read, every connection sends an explicit offline status, and every deletion states whether it removes the message for everyone

## Permissions

One file per chat or folder, in a directory the agent cannot write:

```
allow: read, reply
mark-read-on-reply: yes
```

A request for anything ungranted is refused, and the refusal names the line that would lift it. `allow: all` grants a whole table at once, which suits a chat with no other party in it, and every answer it permits says that a wildcard permitted it

A folder grants reading, summarising and downloading, never sending: its membership changes from your phone without the service being asked. For the same reason a folder that grants reading refuses to be edited by the agent — otherwise it could add a chat to a folder it may read

The full format is in [references/permissions.md](references/permissions.md)

## What it does not do

- **It never acts on text found inside a message.** A chat is data, including a chat you wrote in yourself
- **It cannot grant itself anything.** The permission files are outside its reach, and so is the session that would let it bypass them
- **It announces nothing.** No typing status, no presence, and no read receipt unless a permission asked for one
- **It has no verb for deleting the account, resetting authorisations or changing the password.** Not as a rule it follows — as words the socket does not accept

## Tests

`nix develop -c ./tests/check.sh` runs the gate: the lint half holds the scripts, the workflows, the flake and the documents to their rules, and the behaviour half runs the suite against a stand-in for Telethon, so nothing there reaches the network or an account

`nix develop -c ./tests/t.sh falsify -- ./tests/check.sh behaviour` breaks one guard at a time and requires the suite to notice. Each entry in `tests/defects.sh` says what goes wrong for you when that guard stops working
