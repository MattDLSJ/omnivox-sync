# Install this project

**This file is the whole handoff.** Give an AI coding agent this file, or the
link to it, and it has everything it needs to get from nothing to a working
install. It is deliberately short; the long brief is `START-HERE.md`, inside
the repository, and you read that once you have the repository.

---

## Prompt

You are installing a command-line tool for me on this machine. I am a cégep
student, not a developer. Work through this in order and do not skip ahead.

### 1. Get the code with git. Not as a ZIP.

    git clone https://github.com/MattDLSJ/omnivox-sync.git
    cd omnivox-sync

The repository is public, so that needs no account, no sign-in and no
credentials. If it fails, it is a network problem or a typo, not a permissions
one: read the error out to me rather than reaching for `gh auth login`, which
will not help.

**Do not download the ZIP from the web page, and do not accept one if I hand
you one.** A ZIP has no `.git` folder, and three things break the moment it is
missing, none of them loudly:

- `make update` cannot fetch fixes, so the copy is frozen at the day it was
  downloaded, forever.
- `make report`, which is how a fix here gets back to everyone else, has
  nothing to compare against and cannot tell what changed.
- `make install-hooks` has no repository to install a hook into.

If I have **already** unzipped a copy and set it up, do not throw it away. My
credentials and my timetable are in `.env` and `config.yaml`, and those are
the only files that took real work. Adopt the history in place instead, from
inside that folder:

    git init
    git remote add origin https://github.com/MattDLSJ/omnivox-sync.git
    git fetch origin
    git reset --mixed origin/main

Nothing in the working tree is touched by that. `git status` afterwards shows
whatever I had actually changed, which is the thing worth knowing anyway.

### 2. Then follow START-HERE.md

Open `START-HERE.md` in the folder you just cloned, read all of it, and do
what it says. It covers macOS and Windows, it names the traps, and it is
written for exactly this situation. It is the real instructions; this file
only exists to get you to it.

### 3. Two things it will ask of you later, so know them now

**Report what you had to fix.** This runs on other people's machines, at other
colleges, in another language, and every one of those turns up something the
author never hit. When something breaks and you work around it, `make report`
writes up what happened and `make send-report` sends it. That is not
bookkeeping: it is how the fix reaches the next person.

**Never put a password in a chat message or in a file yourself.** When
credentials are needed, tell me to type them into `.env` myself, then confirm
the file is filled without printing what is in it.
