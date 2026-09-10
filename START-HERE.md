# START HERE

**Copy this whole file and paste it into a fresh AI coding session opened in
this folder.** That is the setup. There is no installer and no interface: this
is a command-line tool somebody built for their own semester, and the AI is
what turns it into yours.

If you have not got the folder yet, `INSTALL.md` is the shorter file that gets
you one. Clone it with git; a downloaded ZIP looks identical and quietly
cannot be updated.

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

### Before you touch anything

Three of these you check yourself, silently. The fourth is the one question I
actually have to answer, and the installer asks it for you.

1. **That this folder is a git checkout.** Run `git status`. If it answers
   "not a git repository", I downloaded the ZIP instead of cloning, and three
   things are silently broken: I can never pull a fix, `make report` cannot
   see what I changed, and `make install-hooks` has nothing to install into.
   Fix it before anything else, and do not start over from scratch, because
   nothing I have set up needs to be lost:

       git init
       git remote add origin https://github.com/MattDLSJ/omnivox-sync.git
       git fetch origin
       git reset --mixed origin/main

   That last command touches no file in the folder; it only teaches git what
   is already here. Do not ask me for the URL: I do not know it either, and
   the one above is correct.

2. **My operating system.** macOS and Windows both work. The paths, the
   scheduler and one install step differ.

3. **Which cégep I attend, in plain words.** Just ask me, and write the answer
   down; you do not need a URL and neither do I. Omnivox is one product that
   nearly every cégep in Quebec runs, and the only thing that differs is the
   hostname. At step 3 below, `make find-portal` turns the name into the
   hostname and checks it against the live site, so it comes back with the
   college's own name for me to confirm. It needs the environment from step 1,
   so it cannot run yet.

   Give it my **full** college name, not a word of it. Several colleges share
   a first word across different campuses, and a partial name is refused
   rather than guessed at.

   **Do not ask me what language my Omnivox is in.** `make find-portal` reads
   that off the public sign-in page and tells you. It is a question about a
   config file wearing the costume of a question about my life: I would not
   know why you were asking, and I cannot judge the cost of answering wrong,
   which is that every course reports no documents forever. If it comes back
   English, `find-portal` prints exactly what to do about it, and that part is
   your job rather than mine.

4. **Python 3.11 or newer.** `python3 --version` on macOS, `py --version` on
   Windows. Show me what it says.

If any of those is a no, stop and tell me what it would take. Do not improvise
around it.

### Rules

- **Never ask me for a password in the chat, and never type one into a file
  yourself.** You do not need my password at any point: I sign in myself, in a
  real browser window, at step 4. If we later add one for unattended re-login,
  `make setup-omnivox` asks me directly and writes it without showing you.
- **Never invent a value in `config.yaml`.** Course codes, folder names and
  notebook names come from the discovery step or from me.
- **Run the offline tests ONCE**, right after the environment is built, and
  remember the number. Use the project's own Python, never a bare `pytest`,
  which is a different installation and fails on the imports:

      .venv/bin/python -m pytest -m "not live"          macOS
      .venv\Scripts\python -m pytest -m "not live"       Windows

  That number is the baseline. Setting this up changes no code, so there is
  nothing to re-verify: run it again only if you actually edit a source file,
  then compare. A thousand tests take real time, and a setup that blocks for a
  minute at every step is a setup people abandon. Tests that only apply to
  another operating system report as **skipped**, not failed; a handful of
  skips on Windows is correct and is not something to fix.
- **Never commit or push, and never run `make publish` or
  `make public-snapshot`.** Those send code to the public repository and belong
  to whoever maintains it, not to me.
- **Show me real output** rather than telling me it worked. Once, at the end,
  not after every command.
- **Actually run a command in the same turn you mention it.** Do not tell me
  you are about to run something and then stop. Some agent tools end the turn
  there and nothing happens until I send another message, which turns a
  ten-minute setup into an hour of me typing "ok". If something is genuinely
  slow, say what you are running, run it, and tell me the result when it
  comes back.
- **Write down anything you had to fix.** Not at the end, when you have
  forgotten: keep a running note as you go, and turn it into a report at the
  end with `make report`. Details below, and it matters more than it sounds
  like it does.

### Normally, one command does all of this

    python3 install.py          macOS
    py install.py               Windows

That is the whole setup. It builds the environment, fetches the browser,
creates the config files, finds my college, signs me in, discovers my courses,
asks which features I want, and runs the first sync. It stops three times, for
the three things only I can answer, and it is safe to run again because every
step skips itself if it is already done.

**Run that first.** If it works, skip to "Deal with the mess I already have"
below and do the rest of your job from there.

Everything between here and there is the same sequence written out by hand,
for when the installer fails and you need to know what it was trying to do. It
is a reference, not the normal path. Do not work through it step by step
unless something has actually gone wrong.

**Do not check in at each step. Run it.**

There are exactly two moments where you stop, and both are windows on my
screen rather than questions in this chat:

1. **A settings page**, which asks which college I attend and which three
   features I want. Four answers, once.
2. **Signing in to Omnivox**, in the browser window it opens next.

That is the whole list. Everything else you do, and you tell me about
afterwards. Do not ask me to approve a folder name, confirm a version number,
choose between two commands, or say yes before continuing. If something goes
wrong, say that immediately, in plain language, and keep going where you can.

---

### The same thing, step by step, for when it breaks

**1. Build the environment.**

macOS: `make venv`, in Terminal.

**On Windows, use PowerShell**, and say so to me, because no single Windows
shell runs every command in this file as written: `cmd` has no `cp`, and a
bash-like shell treats the backslashes in `.venv\Scripts\python` as escapes and
reports a command called `.venvScriptspython`. If you are driving a bash-like
shell anyway, write those paths with forward slashes, `.venv/Scripts/python`,
which both Python and Windows accept.

Windows has no `make` and puts Python elsewhere, so:

    py -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python -m playwright install chromium

The last one downloads a browser, around 550 MB. Expected.

Everywhere below, on Windows replace `make X` with the command it wraps and
`.venv/bin/python` with `.venv\Scripts\python`.

**2. Make my config.**

    cp config.example.yaml config.yaml
    cp .env.example .env
    cp .private-patterns.example .private-patterns

Then set my portal name under `school:`, using `make find-portal`, which takes
the name of my college and checks the answer against the live site.

Nothing goes in `.env` by hand. Step 4 offers to fill in the credentials from
what I type into the browser, which is the only part of `.env` most people
ever need.

**3. Start the slow downloads, and then leave them running.**

`ffmpeg` and LibreOffice, for the PowerPoint and Word to PDF conversion.
Together they are most of a gigabyte and several minutes, and **nothing until
the first sync needs either of them**, so they must not be something I sit and
watch. Start them and go straight on to step 4 while they download.

Clear any administrator prompt first: a dialog stealing focus while I am
typing a six-digit code is how step 4 gets failed and repeated. And never
background anything that will ask me for something; if it needs me, it is not
background work.

If LibreOffice is not ready by the first sync, that is not a failure. The
conversion is logged, the original file is kept, and the run carries on.

**4. Sign in once, in a real browser.**

    make login

A browser window opens on Omnivox. **I** type my student number and password
into it, Omnivox e-mails me a six-digit code, and **I** type that in too. You
never see any of it.

**Tell me before I start that I have to tick "J'utilise un appareil de
confiance"** before validating the code. Miss it and today works perfectly,
then every scheduled run afterwards gets challenged, and it quietly stops
working tomorrow.

At the end it asks whether to save what I typed. **Say yes, and tell me why:**
the stored profile keeps the trusted-device cookie, which is what stops the
codes, but not the signed-in session, which dies with the browser. Measured
over 93 runs on the author's machine: 114 sign-ins, zero reuses. So without
saving them this works when I run it by hand and stops the moment it is left
alone.

**Do not ask me to type my password to you, and do not run a command that
makes me type it a second time.**

`make login` also discovers my courses and writes them into `config.yaml` in
the same session, so there is usually nothing to do at step 6.

**5. Ask me which parts of this I want.**

    make setup

A page opens in my browser with three questions: whether I use NotebookLM,
whether I want lecture recording, and whether I want notifications. It writes
my answers into `config.yaml`. Do not ask me these in the chat instead; the
page explains what each choice costs, which a chat message does not.

Everything else has a sensible default and lives behind `make settings`, which
I can open any time. Mention that it exists, then move on.

**6. Check the courses it found.**

Step 4 already found them and wrote them into `config.yaml`. If it did not, or
to redo it:

    make discover

Do not ask me whether the folder names look right. They come from the names
Omnivox already uses, one line changes any of them later, and nothing about
them is irreversible. **List them in the summary at the end** and carry on.

Never print a block for me to paste somewhere. If a command produces
configuration, it writes the configuration. Discovery adds courses it has not
seen and leaves the ones already there exactly alone, so nothing set by hand
is at risk, and the previous file is kept as `config.yaml.bak` regardless.

**7. Rehearse, then run.**

    make dry-run

Nothing downloads, nothing is written. Read that output yourself, and if it
looks right run `make sync` for real without asking me first. If it looks
wrong, that is exactly when you stop and tell me.

The first real run backfills the whole semester so far, which for a normal
course load is a hundred-odd files. That is correct, not a bug.

**8. Deal with the mess I already have.**

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

**9. Only once a manual sync works**, ask whether I want it scheduled.
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

On Windows, or anywhere without `make`, that is three commands:

    git pull --ff-only
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python scripts\whats_new.py

The second matters. Dependencies change between releases, and pulling alone
leaves me with new code and old packages, which fails in a way that looks like
a bug in the project. The third prints what actually changed in the releases I
just pulled, which is the only way to notice that a fix I patched around by
hand last week is now in the project properly.

If the pull refuses because I have my own commits, tell me to put them on a
branch or stash them rather than forcing anything. And if it refuses because
this is not a git repository, go back to confirmation 1.

### The three questions on the setup page, explained

You do not ask me these. The page does, and it explains each one. This is here
so that you can answer if I ask you about them.

**NotebookLM.** `"off"` does nothing at all, for somebody who does not use it.
`"staging"` copies new files into `<course>/_to_upload/` for me to drag in
myself, which keeps all the downloading, converting and filing and loses only
the drag. `"auto"` needs a separate command-line tool installed and a notebook
created by hand per course, named exactly as in `config.yaml`; if that
notebook is missing it silently stages instead, which is why nobody should
start there.

**Lecture recording.** macOS only, needs a 1.5 GB speech model, and stays
completely inert until a class timetable is in `config.yaml`. Off unless I ask.

**Notifications.** Desktop ones are macOS only. For my phone instead, tell me
about `NTFY_TOPIC` in `.env`: I pick a random string nobody would guess, put
it there, and subscribe to the same name in the ntfy app. An ntfy topic has no
password, so anyone who knows the name can read my notifications and send me
fake ones, which is why it lives in `.env` and never in `config.yaml`.

### Send back whatever you had to fix

Read this even if everything worked, because it changes what you should be
doing during the steps above.

This project was written by one student, for one machine, at one cégep, in
French. It is now being run at other colleges, on Windows, in English. Every
one of those turns up something its author has never seen and cannot see: he
does not have my laptop, my college's portal, or my error messages. The only
information that exists about any of it is what the person who hit it writes
down.

So **keep a running note while you work**. Not a summary at the end, when the
exact error has scrolled away. Every time you work around something, note the
command, the error, and what you changed. Include the ones you are unsure
about; a workaround that happens to work is somebody else's bug report next
month.

Then:

    make report

That writes `field-report.md` with the machine-knowable parts already filled
in: my OS, my Python, my college, whether the tests pass, and a diff of every
line that differs from the released code. It leaves a handful of questions
that only you can answer. Answer all of them, then:

    make send-report

which checks the finished report for **my** private data and refuses to send
if it finds any, then opens it as an issue on the repository we cloned.

The one question worth thinking about rather than filling in: **does this
belong upstream?** Editing `config.yaml` to name my courses is setup, and
there is nothing to report. Patching a source file because my college's portal
is in English, or because a path only works on macOS, is a fix that everybody
else needs. Say which one it was, and say why. Guess out loud if you are not
sure; a wrong guess with reasoning is useful and a blank is not.

If `make send-report` cannot reach GitHub, do not throw the report away. Tell
me where the file is and that I should send it to whoever gave me this.

### When you are done

Tell me in five lines or fewer: what runs now, where my files are, what I still
have to do by hand, and the one command to sync right now without waiting.

Then ask what I want to do with it first.
