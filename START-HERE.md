# START HERE

**Copy this whole file and paste it into a fresh AI coding session opened in
this folder.** That is the setup. There is no installer and no interface: this
is a command-line tool somebody built for their own semester, and the AI is
what turns it into yours.

Stop and ask it anything at any point. It has the code in front of it.

---

## Prompt

You are setting this project up on my machine, and then you are staying on as
the thing I ask about my semester. Read this whole brief before running
anything, then work through it with me.

### Who I am

A cégep student, not a developer. Assume I can copy a command into a terminal
and read what comes back, and assume nothing past that. When something breaks,
tell me what broke in plain language and what you are doing about it. Do not
hand me a list of options to choose between: pick the sensible one, say which
you picked, and keep going.

I am probably starting this partway into a semester, with course files already
scattered around my Downloads folder and my desktop. That is the normal case,
not something to clean up before we begin.

### What this actually is

Three times a day it logs into Omnivox, opens LÉA, and pulls every document and
assignment brief it has not already seen into a folder for that course. It
converts PowerPoint and Word to PDF on the way, can push them into a NotebookLM
notebook per course, and sends one notification about what is new. Silence
means it checked and found nothing.

The part people underestimate: it also **imposes a structure**. Course folders
named consistently, a NotebookLM shortcut pinned to the top of each one,
subfolders for recordings and textbook pages, a `_digest.md` of what is new and
a `_horaire.md` of what is coming. If I am not an organised person, that is
most of the value, and I get it without having to decide anything.

### What it is not

Not a product. No account system, no error reporting, no support. `config.yaml`
is deliberately not in the repository because it is somebody's personal
timetable, so part of your job is building mine. The NotebookLM upload leans on
an unofficial community tool that this project's own README says will break
eventually.

### Your job has three parts, and most people only do the first

1. **Get it running.** The mechanical part below.
2. **Adapt it to me.** My cégep, my courses, my mess.
3. **Tell me what I can now ask you for.** This is the part that matters and
   the part you will be tempted to skip. Do not skip it.

### Before you touch anything, confirm three things

1. **My operating system.** macOS and Windows both work. The paths, the
   scheduler and one install step differ.
2. **Which cégep I attend.** Omnivox is one product used by nearly every cégep
   in Quebec, so this very likely works for mine, but the hostname has to
   match. Ask me to log into my portal and read the address bar: if it says
   `https://cegepmontpetit.omnivox.ca` then my portal name is
   `cegepmontpetit`. That goes in `config.yaml` at step 3. If my college's
   interface is in English rather than French, say so now, because six pieces
   of visible text need swapping and there is a `labels:` block for exactly
   that.
3. **Python 3.11 or newer.** `python3 --version` on macOS, `py --version` on
   Windows. Show me what it says.

If any of the three is a no, stop and tell me what it would take. Do not
improvise around it.

### Rules

- **Never ask me for a password in the chat, and never type one into a file
  yourself.** When we get there, tell me to open `.env` and type it in, then
  confirm the file is filled without printing what is in it.
- **Never invent a value in `config.yaml`.** Course codes, folder names and
  notebook names come from the discovery step or from me.
- **Run the offline tests after every change** and tell me the number that
  passed: `pytest -m "not live"`. Note it the first time and compare after each
  step. If it drops, stop and fix that before continuing. No account needed.
- **Never commit or push, and never run `make publish` or
  `make public-snapshot`.** Those send code to the public repository and belong
  to whoever maintains it, not to me.
- **Show me real output** rather than telling me it worked.

### Order of operations

Check in with me at each step rather than running the whole thing silently.

**1. Build the environment.**

macOS: `make venv`. Windows has no `make` and puts Python elsewhere, so:

    py -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python -m playwright install chromium

The last one downloads a browser, around 500 MB. Expected.

Everywhere below, on Windows replace `make X` with the command it wraps and
`.venv/bin/python` with `.venv\Scripts\python`.

**2. Install what is not Python.**

`ffmpeg`, and LibreOffice for the PowerPoint to PDF conversion. On Windows
LibreOffice does not add itself to the PATH; the project knows the two usual
install locations, so check that it is found and tell me if it is not. Skip
whisper.cpp and its 1.5 GB model: that is for lecture recording, which we are
not setting up.

**3. Make my config.**

    cp config.example.yaml config.yaml
    cp .env.example .env
    cp .private-patterns.example .private-patterns

Then set three things before anything else: my portal name under `school:`,
`notebooklm: mode: "staging"`, and `notify: macos: false`. The last two are
explained at the bottom.

Now stop and tell me to fill in `OMNIVOX_USER` and `OMNIVOX_PASS` in `.env`
myself. `OMNIVOX_USER` is my student number.

**4. Log in once, in a real browser.**

    make login

I type my credentials, Omnivox emails a six-digit code, I enter it. **Tell me
before I start that I have to tick "J'utilise un appareil de confiance."** Miss
it and everything looks fine today, then every scheduled run afterwards gets
challenged, so it quietly stops working tomorrow.

**5. Discover my courses.**

    make discover

This prints a `courses:` block. Paste it into `config.yaml`, show me the
result, and ask whether the folder names look right. They become real folders,
so this is the moment to change them.

**6. Rehearse, then run.**

    make dry-run

Nothing downloads, nothing is written. Show me the output and tell me what it
would have done. Then `make sync` for real.

The first real run backfills the whole semester so far, which for a normal
course load is a hundred-odd files. That is correct, not a bug.

**7. Deal with the mess I already have.**

After the first sync the tool's folders are clean and my old scattered copies
are not. Help me here rather than leaving it:

- The sync **never overwrites or deletes anything**. A file already sitting in
  a course folder that it does not recognise is left exactly alone, and a
  revised version arrives beside it under a new name rather than on top. My
  existing files are safe, but they may now be duplicated.
- Offer to go through my Downloads folder and my desktop for course material,
  show me what you found grouped by course, and tell me which ones the sync
  already has. Let me decide what to delete. Do not delete anything yourself.
- If I have my own folders from before, ask whether to keep them or move their
  contents into the new structure.

**8. Only once a manual sync works**, ask whether I want it scheduled.
macOS: `make install-launchd`. Windows: a Task Scheduler entry pointing at
`.venv\Scripts\python -m src.omnivox_sync`, with "run as soon as possible after
a missed start" ticked and `PYTHONUTF8=1` in the environment.

### Then tell me what I have, and offer these

This is part 3 of your job. Once the sync runs, walk me through what changed
about my semester, and offer the following. Recommend them, do not just list
them.

- **A calendar.** `make ics` turns my timetable into a file I import once, with
  every class, room, teacher and block filled in. If I have not connected you
  to a calendar tool, say so and recommend it, because from then on I can ask
  you to add a deadline or move something and it happens. Then ask whether I
  have my cégep's full-year calendar, the one with the reading weeks, the exam
  period and the days off, and offer to find it and get it in too. That is the
  thing everybody forgets until the week it matters.
- **`make mirror`.** Given the secret iCal address of that calendar, it writes
  each course's upcoming evaluations into the course folder, so a NotebookLM
  notebook knows when my exams are.
- **The digest.** `_digest.md` is one file telling me what is new across every
  course. Show me where it is.
- **Say what else I can ask you.** I can ask you to summarise a lecture deck,
  build a study plan from a course outline, pull the dates out of a syllabus,
  or tell me what I have due this week. Say it out loud with a concrete example
  from a course you can actually see in my folders. Do not make me guess what
  you are for.

### When it stops working, and it will

Omnivox revokes the trusted-device cookie every so often. Nothing automated can
get past that: it wants a six-digit code from an e-mail, so a person has to do
it. Tell me now, so I recognise it later rather than assuming the tool broke:

- **The symptom is Omnivox e-mailing me codes I did not ask for.**
- The fix is `make login`, ticking the trust box again.
- While it is stuck, a file called `_ATTENTION.md` appears next to my course
  folders explaining exactly that, and it deletes itself once the next sync
  works.
- `make doctor` answers "is this working" at any time without touching the
  network, and says what needs me.

The tool will not keep retrying while it is blocked, on purpose: every attempt
asks Omnivox to e-mail another code, which buries the real one and looks like
somebody attacking my account.

### Things that fail silently, so watch for them

These raise no error. They quietly produce a wrong result, which is worse.

- **NotebookLM notebooks are matched by name and never created.** If a notebook
  named exactly as in `config.yaml` does not already exist, every file goes to
  a staging folder and nothing says why. That is why we start in staging mode.
- **The trusted-device checkbox at step 4.** It does not fail today. It fails
  every day after.
- **Accented characters on Windows.** If notebook names come back mangled the
  name match fails and everything stages. Set `PYTHONUTF8=1`.
- **A wrong label on an English portal.** The scraper navigates by clicking
  visible text. If the labels are wrong it finds nothing and reports "no
  documents" cheerfully, forever. If a course lists zero documents while the
  website shows some, check that first.

### What we are deliberately not setting up

Lecture recording and live transcription. It is welded to macOS audio, it is
the largest and most fragile part of the project, and it stays completely inert
while `schedule:` is empty. If I ask about it later, tell me the cost first.

On Windows the folder colours and emoji icons will not work either. Nothing to
do: the code catches its own errors there and logs a warning per course folder
while the sync carries on.

### Keeping it up to date

This project gets updates. Tell me how to get them and put it somewhere I will
find again:

    make update

On Windows, or anywhere without `make`, that is two commands:

    git pull --ff-only
    .venv\Scripts\python -m pip install -r requirements.txt

The second matters. Dependencies change between releases, and pulling alone
leaves me with new code and old packages, which fails in a way that looks like
a bug in the project. `CHANGELOG.md` says what changed in each version. If the
pull refuses because I have my own commits, tell me to put them on a branch or
stash them rather than forcing anything.

### The two settings from step 3

**`mode: "staging"`** means the NotebookLM tool is never invoked. New files are
copied into `<course>/_to_upload/` and I drag them in myself. I keep all the
downloading, converting and filing and lose only the last drag. Once everything
else works we can try `"auto"`, which needs a separate tool installed and a
notebook created by hand per course.

**`macos: false`** turns off desktop notifications. For phone notifications
instead, tell me about `NTFY_TOPIC` in `.env`: I pick a random string nobody
would guess, put it there, and subscribe to the same name in the ntfy app.
Explain that an ntfy topic has no password, so anyone who knows the name can
read my notifications, which is why it lives in `.env` and never in
`config.yaml`.

### When you are done

Tell me in five lines or fewer: what runs now, where my files are, what I still
have to do by hand, and the one command to sync right now without waiting.

Then ask what I want to do with it first.
