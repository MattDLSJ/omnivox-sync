# school-automation

> **Setting this up for the first time?** Open
> [START-HERE.md](START-HERE.md), copy the whole file, and paste it into an AI
> coding session opened in this folder. It walks the setup end to end on macOS
> or Windows. Everything below is the reference for once it runs.

Automates the repetitive parts of a cégep workflow. **All four milestones are
implemented.** Design doc:
[2026-08-06-school-automation-design.md](docs/superpowers/specs/2026-08-06-school-automation-design.md).

## What it does

Weekdays at 07:30 and 12:15, a `launchd` job:

1. **Syncs Omnivox LEA** (M1) — downloads every document it has not seen into the
   matching course folder, converting Office formats to PDF.
2. **Uploads to NotebookLM** (M2) — pushes new files into the notebook mapped to
   that course, falling back to a `_to_upload/` staging folder on any failure.
3. **Sends a digest** (M3) — one notification covering new documents, unread MIO,
   "Quoi de neuf" items and upcoming dates, with important ones flagged first.
   Silence means it checked and found nothing.

A separate 5-minute job **records scheduled classes** (M4) and feeds the audio
back through the same upload pipeline. It stays completely inert until
`schedule:` is filled in.

| Milestone | Status |
|---|---|
| M1 Omnivox sync | done — verified live (165 documents, 7 courses) |
| M2 NotebookLM upload | done — verified live (real upload + dedup + staging) |
| M3 Digest | done — verified live (26 items, 4 flagged important) |
| M4 Recorder | done, **inert** until `schedule:` is populated |

## Before you start

**macOS is the tested platform; the Omnivox half runs on Windows too.** The
sync, conversion and upload are portable. Scheduling is `launchd`, the folder
presentation uses Finder tags and `xattr`, and several paths assume Homebrew at
`/opt/homebrew`, so on Windows you replace the scheduler with Task Scheduler
and lose the folder colours, which the code degrades to a warning. The lecture
recorder is macOS only and stays inert until `schedule:` is filled in. Scheduling is `launchd`, the folder presentation uses
Finder tags and `xattr`, and several paths assume Homebrew at `/opt/homebrew`.

**It is also wired to one school.** The scraper talks to
`cegepmontpetit.omnivox.ca`, so running the pipeline end to end needs an Omnivox
account at cégep Édouard-Montpetit. Without one you can still read the code and
run `make test-unit`, which is the whole offline suite and needs no account.

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
| `make check-private` | Fail if anything personal is in the repo. `STAGED=1` for staged only, `HISTORY=1` to include commit messages. |
| `make install-hooks` | Point git at `.githooks/`, so the check runs before every commit and on every commit message. |
| `make public-snapshot` | Build a shareable copy: the tree at HEAD, one commit, no history. |

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

**`make public-snapshot` is how the code leaves.** Two things cannot be fixed by
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
make public-snapshot
```

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
`schedule:` and the term dates, so it can be imported into Google Calendar or
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
