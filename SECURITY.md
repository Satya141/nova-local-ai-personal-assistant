# Security

NOVA acts on your computer, your files and your accounts, so security reports matter more than most bugs.

## Reporting a vulnerability

Please report it privately through GitHub: on this repository, open **Security → Report a vulnerability**. Do not open a public issue for it.

Say what an attacker can make NOVA do, and how: the request, page, email or file that triggers it, and what happened. A report is acknowledged within a week.

## What counts

Anything that gets around the rules NOVA is built on (see [AGENTS.md](AGENTS.md)), for example:

- outside content (a web page, an email, a window, a GitHub issue) making NOVA take an action without the user's confirmation;
- a tool running a command, a program or a path it should refuse;
- deleting anything other than to the Recycle Bin, or touching files outside the home folder;
- the API answering without its token, or the phone listeners accepting an unpaired device or a non-private address;
- account tokens reaching the model, the logs, the database or the API;
- memory storing passwords, keys, card or ID numbers;
- the microphone or the screen being used without the user's action.

## Supported versions

Only the latest commit on `main` is supported.
