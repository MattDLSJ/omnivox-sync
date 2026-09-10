# Install this project

**This file is the whole handoff.** Give an AI coding agent this file, or the
link to it, and it has everything it needs to get from nothing to a working
install. It is deliberately short; the long brief is `START-HERE.md`, inside
the repository, and you read that once you have the repository.

---

## Prompt

You are installing a command-line tool for me on this machine. I am a cégep
student, not a developer. Work through this in order and do not skip ahead.

**Two rules for how you work, before anything else.**

**Run a command in the same response you mention it.** Do not tell me you are
about to run something and then stop. Some agent tools end the turn there and
nothing happens until I send another message, which turns a ten-minute setup
into an hour of me typing "ok". If something is slow, say what you are
running, run it, and tell me the result when it comes back.

**Do not ask me to approve each step.** There are two moments where I have to
answer something, both of them windows the installer opens for me. Everything
else you do and tell me about afterwards.

### 1. Get the code with git. Not as a ZIP.

**Clone it into my home folder, and do not ask me where.** `~/omnivox-sync`
on a Mac, `%USERPROFILE%\omnivox-sync` on Windows. Tell me the path you used
and carry on; I do not have an opinion and I should not have to form one.

    cd ~                 # or: cd $env:USERPROFILE   on Windows
    git clone https://github.com/MattDLSJ/omnivox-sync.git
    cd omnivox-sync

**Not wherever your own working directory happens to be**, which is the part
that actually needs saying. An agent doing this cloned it into its own scratch
folder, buried inside a hidden directory belonging to the AI tool itself. That
folder ends up holding my credentials, my signed-in browser profile and a
semester of my documents, and it is a folder that tool is free to clear
without warning. It is also one I would never find.

If a folder of that name is already there and is not empty, that is the one
case where you stop and ask me.

The repository is public, so that needs no account, no sign-in and no
credentials. If it fails, it is a network problem or a typo, not a permissions
one: read the error out to me rather than reaching for `gh auth login`, which
will not help.

If `git` itself is missing, you do not need administrator rights to fix that:
MinGit is a portable download that unpacks into a folder and works. Do that
rather than telling me to go and install something.

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

### 2. Run the installer

One command. It builds the environment, fetches what it needs, finds my
college, signs me in, discovers my courses, asks me which features I want, and
does the first sync.

    python3 install.py          macOS
    py install.py               Windows

It stops twice, and both are windows on my screen rather than questions in
this chat: a settings page asking which college I attend and which three
features I want, and then the browser it opens for me to sign in to Omnivox.
Everything else it does by itself.

**When it opens a page, do not repeat its questions to me here.** Print the
address it gives you and stop. Restating the options in chat invites me to
answer in chat, and an answer typed in chat reaches nothing: the page is still
sitting there waiting for a form submission, and now we are both waiting for
each other. Say "answer it in the browser and tell me when you have", and
wait.

**If you cannot open a window on my screen, say so and hand it to me.** Some
agent tools run commands in a sandbox, where a browser launches somewhere I
will never see it: the command reports success, nothing appears, and I sit
waiting. The installer detects this and stops cleanly with instructions, and
if it does, do not try to work around it. Tell me to open a terminal myself
and run the same command, and pick up from what I report back. Running it a
second time skips everything already done.

It is safe to run again. Every step checks whether it is already done and
skips it, so if anything fails partway, run it again rather than starting
over.

**Watch it, do not drive it.** Your job during this is to read what it prints
and tell me if something looks wrong. If it fails, `START-HERE.md` in the same
folder is the long version, with every step spelled out and every known trap
named; use it to work out what went wrong rather than as the normal path.

### 3. Two things it will ask of you later, so know them now

**Report what you had to fix.** This runs on other people's machines, at other
colleges, in another language, and every one of those turns up something the
author never hit. When something breaks and you work around it, `make report`
writes up what happened and `make send-report` sends it. That is not
bookkeeping: it is how the fix reaches the next person.

**Never put a password in a chat message or in a file yourself.** When
credentials are needed, tell me to type them into `.env` myself, then confirm
the file is filled without printing what is in it.
