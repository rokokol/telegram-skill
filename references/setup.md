# What the service needs, and the first login

`tg-agentd` is meant to run as a systemd unit. That is not a packaging preference: it is where the secret lives. A unit with `DynamicUser=yes` holds its credentials in a namespace no other process shares, and its state under `/var/lib/private`, which unprivileged users cannot enter. The agent reaches the account through the socket and never through the session file, because the session file is not visible to it

## What the unit provides

- **Credentials**, as files named `api_id` and `api_hash` in `$CREDENTIALS_DIRECTORY`. The service reads them there and nowhere else, so the pair never reaches an argument list or a child process's environment
- **A state directory**, in `$STATE_DIRECTORY`, holding the session. It is a file rather than a string because Telethon caches each entity's access hash there, and a permission naming a chat by identifier cannot be resolved without that cache
- **A socket**, either passed by systemd or named with `--socket`. A passed socket is the one to prefer: its owner and mode are the unit's to set, and a service under a dynamic user cannot usefully set them itself
- **A permissions directory**, given with `--permissions`, which the service only reads
- **An outbox**, given with `--media-dir`, which is the only place a download may land

## The api pair

The pair comes from [my.telegram.org](https://my.telegram.org), under API development tools. One application per phone number, and the form validates on the server without naming the field it rejected: a short name shorter than five characters, a URL it dislikes or a platform left unselected all return the same blank error

The pair cannot be rotated. Telegram issues one per number and documents no reset, so a leaked `api_hash` stays leaked — which is the reason it is kept where the agent cannot read it

## The first login

Signing in is interactive by definition: Telegram sends a code to the account, and an account with two-step verification also needs its password. It also has to write the session into the same state directory the service reads, which under `DynamicUser` sits inside `/var/lib/private` and is closed to everyone

So the login runs as the service runs, through systemd, with a terminal attached. The module ships `tg-agent-login` for exactly that, and it needs root because it starts a transient unit:

```bash
sudo tg-agent-login
```

Once that has succeeded the session stays valid, and the service starts on its own. A service that finds no session exits 78 and the unit refuses to restart it, because a restart cannot supply a code that arrives on a phone

The login creates a session that appears in the account's device list and sends a "new login" notice to every other device. Naming the device with `--device` is what makes that entry recognisable later, rather than an unlabelled client among the real ones

## What the account sees afterwards

The service announces itself away on every connection. That is a call, not a silence: Telegram reflects an active session's requests as presence, so a client that only avoids saying "online" is still visible while it reads. Nothing else is announced — no typing status, and no read receipt unless a permission asked for one
