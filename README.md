# omnivox-sync

> ### Installing this? Start here, not with the green button.
>
> Give this link to an AI coding agent and let it work:
>
> **https://github.com/MattDLSJ/omnivox-sync/blob/main/INSTALL.md**
>
> That file is the whole handoff: it tells the agent how to get the code and
> what to do next. **Clone it with git; do not download the ZIP.** A ZIP has
> no `.git`, which means it can never receive a fix and can never send one,
> and nothing about it looks wrong until the day that matters.
>
>     git clone https://github.com/MattDLSJ/omnivox-sync.git
>     cd omnivox-sync
>
> Once you have the folder, [START-HERE.md](START-HERE.md) is the long brief
> the agent follows. [INSTALL.md](INSTALL.md) points it there.
>
> **Already had this running before September 2026?** Use
> [CATCH-UP.md](CATCH-UP.md) instead. It brings an existing copy forward
> without losing whatever your AI had to fix, and turns those fixes into a
> report.

Omnivox is the portal nearly every cégep in Quebec runs, and it does not tell
you when something appears. This checks for you, files everything where it
belongs, and hands the result to an AI that can then answer questions about
your own semester.

## Everything it does

**All of it is optional.** `make setup` asks about the three that change the
most; `make settings` has the rest, any time. Nothing here is decided
permanently, and turning something off means it does not run at all rather
than running quietly.

### Getting your material

- **Course documents.** Every new file in LÉA, downloaded into a folder for
  that course. It never overwrites and never deletes: a revised version
  arrives beside the old one under a new name.
- **Assignment briefs.** The Énoncés de travaux, which live in a different
  place from the documents and are the ones with the instructions in them.
- **Office to PDF.** `.docx` and `.pptx` converted on the way in, via
  LibreOffice, so everything downstream sees one format.
- **Textbooks.** Chapters out of i+ Interactif (Chenelière), a chapter at a
  time. Needs a publisher account; off without one.

### Making sense of it

- **NotebookLM.** New files pushed into the notebook for that course, so you
  can ask questions of your own material. Three settings: `auto`, `staging`
  (copied to a folder you drag in yourself), or `off`.
- **A digest.** One `_digest.md` across every course, covering new documents,
  unread MIO, "Quoi de neuf" and upcoming dates, with the important ones
  first. Ranked by simple rules, or by Gemini if you give it a key.
- **Notifications.** One per sync, on your desktop or your phone via ntfy, or
  none. Silence means it checked and found nothing.

### Your semester, not just your files

- **Your timetable as a calendar.** `make ics` turns your courses into a file
  you import once, with every class, room, teacher and block.
- **The calendar mirrored back.** Each course folder gets a `_horaire.md` of
  what is coming, so a notebook pointed at that folder knows when your exams
  are and what you wrote in the event.
- **The start of semester, done by your AI.** This is the part with no button.
  Once the sync has pulled your plans de cours, the AI you set this up with can
  read them and fill your calendar with your evaluations, their dates and their
  weightings, and pull the deadlines out of every assignment brief. It has your
  actual course outlines in front of it, so it is reading them rather than
  guessing. Ask it.

### Lectures

- **Recording and transcription.** Records the class you are in and transcribes
  it with whisper.cpp, then feeds the text back through the same pipeline.
  macOS only, needs a 1.5 GB model, and stays completely inert until you give
  it a timetable. Off unless you ask.

### Keeping itself working

- **Folder presentation.** Consistent course folder names, a NotebookLM
  shortcut pinned to the top of each, subfolders for recordings and textbook
  pages, Finder tags and colours. macOS only; on Windows it logs a warning and
  carries on.
- **Self-updating.** Fast-forwards to the latest release at the start of each
  scheduled sync, and refuses to touch anything if you have made your own
  changes. Off with one setting.
- **Reporting back.** `make report` writes up anything you had to fix, with
  your OS, Python, college and a diff already filled in, and checks it for your
  own private data before `make send-report` files it.
- **Finding your college.** `make find-portal` turns the name of a cégep into
  its Omnivox hostname and verifies it against the live site.
- **Answering "is this working".** `make doctor`, without touching the network.

By default it checks three times a day, at 07:30, 12:15 and 18:30. `make
settings` changes that.

### What it costs on disk

Measured, not estimated, on a real install:

| | |
|---|---|
| Python environment | 210 MB |
| Chromium, for the scraper | 555 MB |
| LibreOffice, for the PDF conversion | 800 MB |
| ffmpeg | 50 MB |
| **Total tooling** | **about 1.6 GB** |
| One semester of course documents | around 100 MB |
| The signed-in browser profile | grows to a few hundred MB over a semester |

Lecture recording adds a **1.5 GB** speech model on top, which is why it is off
unless you ask for it, and why the setup never downloads it speculatively.

## Before you start

**macOS is the tested platform; the Omnivox half runs on Windows too.** The
sync, conversion and upload are portable. Scheduling is `launchd`, the folder
presentation uses Finder tags and `xattr`, and several paths assume Homebrew at
`/opt/homebrew`, so on Windows you replace the scheduler with Task Scheduler
and lose the folder colours, which the code degrades to a warning. The lecture
recorder is macOS only and stays inert until `schedule:` is filled in. Tests
that only apply to another platform skip rather than fail, so a handful of
skips on Windows is correct.

**It needs an Omnivox account, but not a particular one.** Omnivox is one
product that nearly every cégep in Quebec runs, and the only thing that differs
between two installations is the hostname. Set `school: portal:` in
`config.yaml` to yours and the scraper follows; `make find-portal` works it out
from the name of your college and checks the answer against the live site. An
English-language portal additionally needs the `labels:` block, because the
scraper navigates by clicking visible text. Without any account you can still
read the code and run `make test-unit`, which is the whole offline suite.

Install these first. Only Python and `ffmpeg` are needed for the Omnivox side;
the rest are for the recorder, the transcripts and the Office-to-PDF step.

```bash
brew install ffmpeg
brew install --cask libreoffice     # converts .docx/.pptx to PDF
brew install whisper-cpp            # transcription; provides `whisper-cli`
```

Then the transcription model, about 1.5 GB, into `~/.cache/whisper/`:

```bash
curl -L --create-dirs -o ~/.cache/whisper/ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
```

`make fetch-vad` pulls the 864 KB Silero voice-activity model, which is what
lets whisper skip silence instead of hallucinating through it.

There is no `pip install -e .`: the project runs out of its own virtualenv, so
every command is either a `make` target or `.venv/bin/python -m src.<module>`.

## Setup

```bash
make venv
cp .env.example .env                  # then fill in OMNIVOX_USER / OMNIVOX_PASS
cp config.example.yaml config.yaml    # your timetable; not in the repo
cp .private-patterns.example .private-patterns   # then list what must never ship
make install-hooks                    # refuse commits carrying personal data
make login                            # one-time: clear Omnivox identity validation
make discover                         # paste the printed block into config.yaml
make dry-run                          # rehearse: no downloads, no state writes
make sync                             # the real thing
make install-launchd                  # schedule it
```

`config.yaml` is deliberately not in the repo. It holds your courses, your
teachers, your rooms, your group numbers and the coordinates recording is gated
on, which is personal data rather than source. `config.example.yaml` is the
tracked template and carries every key with a safe default.

Phone notifications are optional and off by default. To turn them on, pick any
random string nobody would guess, put it in `.env` as `NTFY_TOPIC=`, and
subscribe to the same name in the [ntfy](https://ntfy.sh) app. An ntfy topic has
no password: whoever knows the name can read your notifications and send you
their own, which is why it lives in `.env` and never in `config.yaml`.

### The one-time `make login` step

Omnivox asks for a 6-digit code emailed to you ("Validation d'identité").
`make login` opens a visible browser so you can enter it — **tick
"J'utilise un appareil de confiance"** before validating. That stores a
trusted-device cookie in `state/omnivox-profile/`, which the scheduled
headless runs reuse, so they are never challenged.

If Omnivox ever revokes that trust, the sync fails loudly with a notification
telling you to run `make login` again. Never copy or move
`state/omnivox-profile/` — a copied profile is not trusted and provokes a
fresh challenge.

## Commands

| Command | What it does |
|---|---|
| `make dry-run` | Report what would happen. Writes nothing. Safe any time. |
| `make sync` | Download, convert, record. |
| `make discover` | Print a paste-ready `courses:` block for a new semester. |
| `make login` | One-time interactive login to clear Omnivox identity validation. |
| `make upload` | Drain the NotebookLM upload queue on its own. |
| `make setup-mic` | Grant and confirm microphone permission (M4 setup). |
| `make mic-test` | Record 5s from the mic and play it back. |
| `make recorder-status` | Show whether the recorder is inert or armed. |
| `make install-recorder` / `make uninstall-recorder` | Schedule / unschedule class recording. |
| `make test` | Full suite (includes live tests; needs `.env`). |
| `make test-unit` | Offline tests only. |
| `make install-launchd` / `make uninstall-launchd` | Schedule / unschedule. |
| `make install-manual` / `make uninstall-manual` | Install / remove the button's on-demand job. |
| `make button` | Build `Sync School.app` beside the course folders, to drag to the Dock. |
| `make books` | Pull the assigned pages out of the i+ Interactif textbooks. |
| `make setup-cheneliere` | One-time interactive login to Chenelière / i+ Interactif. |
| `make ics` | Export the semester schedule as an `.ics` you can import into any calendar. |
| `make mirror` | Copy the School calendar into each course's `_horaire.md` so NotebookLM can read it. |
| `make setup-ics` | Walk you through finding the calendar's secret iCal address. |
| `make sound-check` | Record from your mic and hear the untreated vs treated version. |
| `make fetch-vad` | Download the Silero voice-activity model used by transcription. |
| `make record-now` | Record the class happening right now, ignoring the at-school gate. |
| `make doctor` | Say whether this is working, and what needs a person. No network. |
| `make check-private` | Fail if anything personal is in the repo. `STAGED=1` for staged only, `HISTORY=1` to include commit messages. |
| `make install-hooks` | Point git at `.githooks/`, so the check runs before every commit and on every commit message. |
| `make public-snapshot` | Create the public repository, once. |
| `make publish` | Ship an update to it: `make publish MESSAGE="what changed"`. |
| `make update` | For anyone who installed it: pull the latest release and reinstall deps. |

Flags: `--dry-run`, `--course CODE`, `--headed`, `--discover`, `--login`,
`--retry`, `--manual`, `--wait SECONDS`, `--config PATH`.

Exit codes: `0` success (including "nothing new"), `1` partial failure (some
courses errored), `2` fatal (config, credentials, or login failed, nothing ran),
`3` offline (a scheduled run arms a retry; `--manual` does not), `4` another run
held the lock and this one skipped.

## The button

`make install-manual && make button`, then drag **Sync School** onto the Dock.
It is built beside the course folders (from `base_path` in `config.yaml`, so it
follows the semester) and wears the same green *School* tag as they do. One click runs the whole sequence once: Omnivox,
download, convert, NotebookLM upload, digest.

It is not the same as the scheduled job, in three ways:

* **One press, one attempt.** Offline, it says so and stops. A scheduled run
  would arm a retry for the next window; a button press must not quietly start
  a full sync and upload hours later.
* **It always reports back**, including "Up to date. Nothing new." The 07:30 job
  stays silent in that case on purpose; someone watching the Dock should not.
* **It drains the upload queue even when nothing is new**, so files stranded by
  an earlier failed upload finally go.

The app itself is fifteen lines of shell that call
`launchctl kickstart gui/$(id -u)/com.school.manual`. Going through launchd is
what makes a double-click harmless: launchd will not start a second instance of
a job that is already running.

### Only one run at a time

Every entry point takes `state/omnivox.lock` (an `flock`, so the kernel releases
it on a crash or a `kill -9`). This matters because a headless Chromium does
*not* lock its own profile: two of them will open `state/omnivox-profile/`
together with no error at all, and a headless run will even join a profile that
a headed `make login` is holding, mid-MFA.

A run that finds the lock held logs it and exits, `0` for a scheduled job and
`4` for a button press, which also gets a "a sync is already running"
notification. Nothing waits by default; pass `--wait SECONDS` in a terminal if
you want it to queue behind the running sweep instead.

## Each semester

1. `make discover`, paste the block into `config.yaml`.
2. Update `semester:` and `base_path:`.
3. Create the matching NotebookLM notebooks by hand (automated creation is out of
   scope for v1).
4. `make dry-run` to confirm, then `make sync`.

## How "new" is decided

A document is new when the tuple `(course_code, filename, publish_date)` is absent
from `state/downloads.json`. Omnivox's red badges and stars are deliberately ignored
— this pipeline clears them itself, so they cannot be a source of truth.

Two guards sit behind that:

- **File on disk, no state record at all** → skipped and recorded, not re-downloaded.
  This is the "state file was lost" case.
- **File on disk, but a record exists at a different publish date** → treated as a
  revision and saved as `name (YYYY-MM-DD).ext`, so a re-published document is not
  silently missed.

## When it breaks

Failures notify and log. Start with:

```bash
tail -50 logs/omnivox_sync.log
ls -lt logs/*.png | head        # failure screenshots
```

A layout change on Omnivox shows up as `ParseError` plus a screenshot. The selector
candidate lists near the top of [src/omnivox.py](src/omnivox.py) are the place to fix
it — each is an ordered fallback list, so add the new selector at the front.

Common cases:

| Symptom | Cause | Fix |
|---|---|---|
| `MfaRequired` | Omnivox revoked the trusted device | `make login` |
| `Login did not complete` | password changed, or a new interstitial | check `logs/login-failed-*.png` |
| `ParseError: No courses found` | semester not started, or LEA layout changed | check `logs/courses-parse-failed-*.png` |
| Documents keep re-downloading | `publish_date` is parsing as empty | check the date format on LEA; see `parse_publish_date` |

Two site behaviours worth knowing, both learned the hard way:

- **Never navigate straight to the LEA host.** It logs the session out. LEA is
  entered by clicking "Léa" on the Omnivox homepage (`goto_lea()`).
- **LEA truncates filenames on screen.** The real name comes from the file
  icon's `title` attribute, not the visible text.

## Finder presentation

Course folders are created with an emoji icon and a colour tag, matching the
convention already used on past semesters. Configure once:

```yaml
finder:
  tag: "School"
  color: "green"
courses:
  - code: "350-703-EM"
    icon: "🧠"          # emoji, or "sym:brain" for an SF Symbol
```

New folders are decorated as they are created, so a new semester looks right
without any manual work. It is purely cosmetic — a failure here logs a warning
and never affects a sync.

Under the hood this is **three** xattrs, and all three are required:

| xattr | Holds | If missing |
|---|---|---|
| `com.apple.icon.folder#S` | `{"emoji":"🧠"}` or `{"sym":"cross.fill"}` | no emoji |
| `com.apple.metadata:_kMDItemUserTags` | binary plist of `"School\n2"` | no tag; drop the `\n2` and the tag loses its colour |
| `com.apple.FinderInfo` | 32 bytes, colour label in bits 1–3 of the flags at offset 8 | **Finder draws a plain folder and ignores the emoji entirely** |

That last row is the non-obvious one: setting the icon xattr alone does
nothing visible. Finder also caches folder appearance, so `killall Finder`
may be needed to see a change (it relaunches itself).

`digest.folder` keeps `_digest.md` out of the top-level course list:

```yaml
digest:
  folder: "Other"
```

## Keeping yourself out of it

This repo is one person's timetable wrapped in code, so the interesting leak is
never a credential. The ones that were actually here: a home GPS reading in a
test fixture, a partner's name on a paired audio device, real teachers in real
fixtures, and a commit message naming a class its author had skipped. Every one
of those was written deliberately, by someone documenting a real bug with real
data. No secret scanner flags any of them, because none of them look like a
secret.

Three things keep it out, and none of them rely on remembering.

**Your own data is not tracked.** `config.yaml`, `.env` and `.private-patterns`
are gitignored. What ships is `config.example.yaml` and `.env.example`.

**`make check-private` knows your values without containing them.** It reads
its needles from those three untracked files plus your git commit address, then
scans the tracked tree and fails on a hit, printing file, line and a redacted
form of what matched, so the report is safe to paste anywhere. Copy
`.private-patterns.example` to `.private-patterns` and add what a config file
cannot know: your name, your machine's network name, your student number, the
name of anyone whose phone pairs with your Mac, the coordinates of where you
sleep. `make install-hooks` makes it run before every commit.

The one thing none of it catches: a commit message that tells on you without
naming anything it knows. `skipped Psycho today, 720 m from campus` contains no
name, no address and no coordinate, so no needle matches and it goes through.
This history already carries messages exactly like that. Nothing automatic will
ever catch them, so the rule is a habit rather than a check: commit messages
describe the code, not the day you had.

**`make public-snapshot` is how the code leaves, and `make publish` is how it stays current.** Two things cannot be fixed by
editing a file: a commit message, and the author line on a commit. Both live in
the commit object, both travel with a clone, and rewriting them changes every
SHA while GitHub keeps serving the old objects to anyone who knows one. So the
answer is not to repair a history, it is to not send one. The snapshot exports
the tree at HEAD into a fresh repository with a single commit and an identity
you choose, verifies it, and stops if anything survived. Your own repository
keeps its full history, which is worth having. It just stops being the thing
you hand to other people.

```bash
git config snapshot.name  "your-github-handle"
git config snapshot.email "0000000+handle@users.noreply.github.com"
make public-snapshot                              # once, to create it
git config snapshot.remote <the url it prints>    # so updates find it again
make publish MESSAGE="what changed"               # every time after that
```

The first snapshot starts the public repo at one commit. Every `make publish`
after it adds one more on top, with a version tag and a `CHANGELOG.md` entry,
so people who cloned it can `git pull` instead of starting over. Your private
history is still never read: the public messages are the ones you type at
publish time, and they go through `check-private` before they are committed.

Set `user.email` in this repo to the same noreply address and future commits
stop carrying your real one.

## Files

- `config.yaml` — semester config. The only file that changes between semesters.
- `.env` — credentials. Gitignored, never committed.
- `state/downloads.json` — every document ever downloaded, keyed by
  `(course_code, filename, publish_date)`. Deleting it is safe: the on-disk guard
  prevents re-downloading files already present.
- `state/upload_queue.json` — files waiting for Milestone 2 to upload.
- `state/omnivox-profile/` — Playwright persistent context; keeps the session
  cookie so most runs skip the login form.
- `{base_path}/_digest.md` — human-readable log of everything the pipeline touched.

## Layout

```
src/common.py        config validation, atomic JSON state, notify(), logging
src/omnivox.py       the only module that touches Playwright
src/convert.py       LibreOffice PDF conversion
src/omnivox_sync.py  orchestration + CLI entry point
launchd/             plist template (rendered with absolute paths at install time)
scripts/             launchd install/uninstall
tests/               875 tests; live ones are marked `live` and need .env
```


## NotebookLM (M2)

Uploads go through the community [`notebooklm-mcp-cli`](https://github.com/jacob-bd/notebooklm-mcp-cli):

```bash
uv tool install git+https://github.com/jacob-bd/notebooklm-mcp-cli.git
nlm login          # browser flow, once
```

Notebooks are matched **by name** from `config.yaml`'s `notebook:` field, so the
notebook must already exist — creating them stays manual. Matching normalises
whitespace and case, because real NotebookLM titles pick up stray leading spaces.
`make upload` with `--list-notebooks` prints the names and ids.

This CLI talks to unofficial internal APIs and *will* break eventually. That is
by design: when it does, every file is staged into `<course>/_to_upload/`, you
get one notification, and the pipeline keeps running.

## Textbooks (i+ Interactif)

`make books` logs into Chenelière's i+ Interactif with `CHENELIERE_USER` /
`CHENELIERE_PASS` from `.env` and pulls the pages your teachers assign out of
the digital textbooks, into `books_folder` inside each course folder. Run
`make setup-cheneliere` once first: like Omnivox, it opens a visible browser so
you can log in, and stores the session in `state/iplus-profile/`.

Optional. With no Chenelière credentials in `.env` the step simply does not run.

## Calendar

`make ics` writes the semester's schedule out as an `.ics` file from
`schedule:` and the term dates, so it can be imported into any calendar that
takes an `.ics`, which is all of them: Google Calendar, Outlook, Apple Calendar or
anything else. It is a one-way export: the calendar is never read back.

`make mirror` goes the other way. Given `SCHOOL_ICS_URL` in `.env` (the secret
iCal address of your school calendar: Google Calendar → Settings → your
calendar → Integrate calendar → "Secret address in iCal format"), it writes each
course's upcoming events into `<course>/_horaire.md`, so the NotebookLM notebook
knows when the exams are. `make setup-ics` talks you through finding that URL.
Optional: with no `SCHOOL_ICS_URL` the mirror does not run.

## Recording classes (M4)

Inert until you fill in `schedule:`. When the timetable publishes:

1. Paste the timetable into `schedule:` (`course`, `weekday`, `start`, `end`).
2. Set `record: true` on the courses worth recording.
3. `make setup-mic` to grant microphone permission, then `make mic-test` to hear it.
4. `make install-recorder`.

Optionally set `at_school_check: "ip"` and run `python -m src.recorder --capture-ip`
once while at the cégep to fill in `school_ip_prefix`, so nothing records off campus.

**Hard limitation, stated plainly:** a closed lid means the microphone is off.
Recording cannot continue through sleep. Audio is written in 5-minute segments,
so a lid close costs at most one segment, and the next tick after wake resumes.

Confirming your professors are okay with being recorded is on you, not the software.

## Credit and licence

Built by [MattDLSJ](https://github.com/MattDLSJ), a Sciences humaines student
at cégep Édouard-Montpetit, for his own semester. It grew from one person's
timetable into something that runs at other colleges, on other operating
systems, in another language, and it did that because the people running it
sent back what they had to fix. `make report` is how that works, and it is the
most useful thing you can do with this project after installing it.

MIT licensed: see [LICENSE](LICENSE). Use it, change it, build on it, ship it
commercially if you like. The one condition is that the copyright notice
travels with it.

Not affiliated with Skytech Communications, who make Omnivox, or with your
college. It signs in as you, reads what you can already see, and downloads it.
