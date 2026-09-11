# Install this project

## What this is, in four lines

A **Python** command-line tool that downloads a Quebec cégep student's course
documents from Omnivox. It is a script, not an application.

- **The only requirement is Python 3.11 or newer.** Everything else it needs,
  it installs itself.
- **There is nothing to compile and nothing to build.** No Rust, no CMake, no
  C compiler, no Node, no Emacs. If you believe this project needs any of
  those, you have not read this file, and you should stop and read it.
- Installing it is: clone the repository, run `install.py`, answer two windows.

That block exists because an agent told its user this machine was missing
"Rust, CMake, LLVM/Clang and Emacs" and went looking for installers. None of
those words appears anywhere in this project. It had announced it had reviewed
this guide.

---

## Prompt

You are installing a command-line tool for me on this machine. I am a cégep
student, not a developer. Work through this in order and do not skip ahead.

**Read this whole file before running anything.** It is short on purpose. If
you find yourself installing a compiler or a build system, you are working on
some other project.

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

**The settings page opens on my screen by itself. Do not open it yourself.**
It is a local address, served by the command you just ran, on my computer. If
you have a built-in browser, preview pane, or web tool, it is the wrong tool
here: it renders pages in your environment, not on my desktop, so I see
nothing and you think it worked. This happened on a real install. The agent
said it had opened the setup page, I said "you havent opened anything", and it
spent three more rounds trying to show me a localhost address inside its own
viewer before giving up and pasting the link, which is what it should have
done at the start.

So: run the command, and if I say nothing appeared, paste me the
`http://127.0.0.1:...` address it printed. That is the entire recovery.

**Do not repeat its questions to me here.** Restating the options in chat
invites me to answer in chat, and an answer typed in chat reaches nothing: the
page is still sitting there waiting for a form submission, and now we are both
waiting for each other.

**Do not end your turn to wait for me.** The command has not returned; it is
sitting on the form and it will carry on by itself the moment I submit it. You
do not need me to tell you I am done, and asking me to report back adds a step
that exists only because you stopped. Give me the address, keep the command
running, and tell me what it prints when it comes back.

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

**Never put a password in a chat message, and never open an editor for one.**
The project asks for credentials itself, with a prompt that validates the
value and writes it without it passing through a shell, a history file or you:

    make setup-omnivox

Use that. Do not open `.env` in Notepad, do not paste a template with
`your_password` in it, and do not invent a way round it. One agent did all
three, and a template full of placeholders is a thing people copy in
literally.

---

## For whoever is sending this file

Send the link to **this file**, not to the repository. Tested on two agents: a
repository link made one of them ask what you wanted done with it and wait,
because a repository does not say what it is for, while a link to a document is
unambiguous.

    https://github.com/MattDLSJ/omnivox-sync/blob/main/INSTALL.md

The long brief is `START-HERE.md` inside the repository, which the agent reads
once it has cloned.
