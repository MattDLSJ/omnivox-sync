# School Automation System Design

**Date:** 2026-08-06
**Status:** Approved direction (code-first). Ready for implementation planning.
**Target machine:** macOS (user's Mac), built and maintained via Claude Code.
**Spec location convention:** `docs/superpowers/specs/2026-08-06-school-automation-design.md`

---

## 1. Purpose

Eliminate the repetitive parts of the user's cégep workflow:

1. Downloading new course documents from Omnivox LEA and filing them into local course folders.
2. Converting unsupported formats to PDF.
3. Uploading new materials to the matching NotebookLM notebook.
4. Surfacing important announcements so nothing critical is missed (the automation itself clears Omnivox "new" badges, so this layer replaces that forcing function).
5. Later: automatically recording classes and feeding recordings into the same pipeline.

## 2. Guiding principles

- **Code runs the loop, AI maintains the code.** Recurring jobs are deterministic Python scripts scheduled by `launchd`. Claude Code is used to build and repair them, not to execute the daily sync. Recurring cost: $0.
- **The pipeline must never reduce information visibility.** Anything the scripts download or "read" on the user's behalf must appear in the daily digest.
- **Fail loudly.** Any failure produces a macOS notification and a log entry. Never silently skip.
- **Config-driven semesters.** All course mappings, folders, notebook names, and schedules live in one `config.yaml` updated once per semester. Nothing semester-specific is hardcoded.
- **YAGNI.** v1 does exactly what is specified here. Future ideas are listed in Out of Scope.

## 3. Architecture overview

Four modules sharing a small core, in one private GitHub repo, running on the user's Mac:

```
Omnivox LEA ──▶ [M1 omnivox_sync] ──▶ course folders ──▶ [convert to PDF]
                     │                                        │
                     │                                        ▼
                     │                              [M2 notebooklm_upload] ──▶ NotebookLM
                     ▼
              [M3 digest] ──▶ morning notification (Mac + optional phone)

class schedule + "at school?" check ──▶ [M4 recorder] ──▶ course folders ──▶ M2
```

`launchd` triggers M1 (which chains conversion, M2, M3) on weekday mornings and again midday. M4 gets its own watchdog job, installed only once the fall schedule exists.

## 4. Repository layout

```
~/Documents/Programming/school-automation/   # repo root (see location note below)
├── config.yaml                        # semester config (courses, paths, schedule)
├── .env                               # OMNIVOX_USER, OMNIVOX_PASS (gitignored)
├── .gitignore                         # .env, logs/, state/, __pycache__
├── requirements.txt
├── src/
│   ├── common.py                      # config loading, state store, notify(), logging setup
│   ├── omnivox_sync.py                # M1 (entry point: also chains M2 and M3)
│   ├── convert.py                     # LibreOffice conversion helpers
│   ├── notebooklm_upload.py           # M2
│   ├── digest.py                      # M3
│   └── recorder.py                    # M4
├── launchd/
│   ├── com.school.sync.plist
│   └── com.school.recorder.plist      # installed in Milestone 4 only
├── state/                             # gitignored JSON state
│   ├── downloads.json
│   ├── uploads.json
│   └── announcements.json
├── logs/
└── docs/superpowers/
    ├── specs/                         # this document
    └── plans/                         # implementation plans (one per milestone)
```

Location note: the repo lives at `~/Documents/Programming/school-automation`. One 10 second check before creating it: if this Mac syncs Desktop & Documents to iCloud (files in Documents show cloud icons in Finder), git repos inside Documents sync badly and iCloud can evict files that `launchd` jobs need. In that case, place the repo at `~/Programming/school-automation` instead (outside Documents) and use that path everywhere in this document. The school folder itself stays where it is; the repo only writes into it.

## 5. Configuration

### 5.1 `config.yaml` schema

```yaml
semester: "Automne 2026"
base_path: "~/Documents/School/Cegep Automne 2026"

courses:
  # Filled at semester bootstrap (see 5.3). Example shape:
  - code: "601-101-MQ"                       # as shown on Omnivox
    omnivox_name: "Écriture et littérature"  # exact display name on LEA
    folder: "Écriture et littérature"        # subfolder under base_path
    notebook: "Écriture et littérature - Cegep Automne 2026"
    record: false                            # M4 opt-in per course

schedule: []
  # Filled once the fall schedule is published. Entry shape:
  # - course: "601-101-MQ"
  #   weekday: "tuesday"        # lowercase english weekday
  #   start: "10:00"
  #   end: "12:00"

notify:
  macos: true
  ntfy_topic: ""            # optional ntfy.sh topic for phone push; empty = disabled

digest:
  ranking: "rules"          # "rules" | "gemini"  (gemini requires GEMINI_API_KEY in .env)

notebooklm:
  mode: "auto"              # "auto" (CLI upload) | "staging" (manual fallback)

at_school_check: "off"      # "off" | "ip" | "ssid"  (used by M4 only)
school_ip_prefix: ""        # filled on first run at school when using "ip"
```

### 5.2 `.env`

```
OMNIVOX_USER=...
OMNIVOX_PASS=...
GEMINI_API_KEY=            # optional, only if digest.ranking = "gemini"
```

Values are copied from the user's existing workflow document. `.env` is gitignored and never committed, even in a private repo.

### 5.3 Semester bootstrap (solves "I don't have my schedule yet")

The fall course list and schedule do not exist yet. The system must not depend on knowing them at build time:

- `python -m src.omnivox_sync --discover` logs in, lists every course visible on the LEA dashboard (code + display name), and prints a ready-to-paste `courses:` YAML block with `folder` and `notebook` pre-filled from the display name.
- The user (or Claude) pastes it into `config.yaml`, adjusts folder names if desired, and creates the matching NotebookLM notebooks manually once (notebook creation stays manual in v1; uploads are automated).
- `omnivox_sync` creates any missing course subfolders under `base_path` on first run.
- `schedule:` stays empty until the timetable is published. M4 is inert while it is empty.
- Build-time testing (August): the login flow and page parsing are developed now. If the Winter 2026 session is still browsable on LEA (via the session selector), it is used as live test data in dry-run mode; otherwise testing completes when fall courses appear.

## 6. Shared core (`common.py`)

- `load_config()` returns a validated config object; exits with a clear error and notification on invalid YAML or missing `.env` keys.
- **State store:** thin JSON read/write with atomic replace (write temp file, then `os.replace`). Each state file is a list of records.
- `notify(title, message, critical=False)`: macOS notification via `osascript`; also posts to the ntfy topic when configured. Used by every module for failures and by M3 for the digest.
- Logging: Python `logging` to `logs/{module}.log`, rotated by size (keep it simple: `RotatingFileHandler`, 1 MB, 3 backups).

## 7. Module 1: Omnivox sync (`omnivox_sync.py`)

**Goal:** download every new distributed document into the right course folder, convert what needs converting, then chain M2 and M3.

**Stack:** Python 3.12+, Playwright (Chromium, headless), PyYAML, python-dotenv.

**Flow:**

1. Launch Playwright with a persistent context stored in `state/omnivox-profile/` (keeps the Omnivox session cookie between runs, so most runs skip the login form).
2. Go to `https://cegepmontpetit-lea.omnivox.ca`. If a login form is present, fill username and password from `.env` and submit. Always navigate from the homepage; never reuse deep links (Omnivox session URLs expire).
3. For each course in `config.courses`: open its "Documents et vidéos" then "Documents distribués" page and parse the document table: filename, publish date, and the download link or document id per row.
4. **New-file definition (deterministic, does not rely on badges or stars):** a document is new if the tuple `(course_code, filename, publish_date)` is absent from `state/downloads.json`. Secondary guard: if a file with the same name already exists in the target folder but the tuple is absent (e.g. state file was lost), skip the download, log a warning, and record the tuple. Omnivox's red badges and stars are ignored as sources of truth because this pipeline itself clears them.
5. Download each new document using Playwright's download API (`expect_download`), bypassing the in-browser Omnivox viewer. No native Save dialog is ever involved; this is precisely the blocker that killed the browser-extension approach in March 2026. If a document only exposes a viewer page, extract the direct file URL from the viewer and fetch it with the session cookies.
6. Save as `{base_path}/{folder}/{original_filename}`. Append the record to `downloads.json` with a timestamp.
7. **Conversion (`convert.py`):** for extensions outside NotebookLM's supported set (supported: pdf, docx, txt, md, images, mp3/wav audio, epub), convert to PDF with `soffice --headless --convert-to pdf --outdir <folder> <file>`. Applies to pptx, ppt, xlsx, xls, odp, and unknown office types. Keep the original beside the PDF. On conversion failure: log, flag in the digest, and if the original format is NotebookLM-supported anyway, still queue the original for upload; otherwise skip upload for that file.
8. Chain M2 with the list of files to upload (PDF version for converted files, original otherwise), then M3 with everything observed this run.

**While on the site, also collect for M3 (read-only):** the "Quoi de neuf" feed items, unread MIO message subjects and senders (subjects only; do not open messages, so they stay unread in the MIO inbox), and any visible due dates or events per course.

**CLI flags:** `--discover` (see 5.3), `--dry-run` (report what would be downloaded/converted/uploaded, no side effects, no state writes), `--course CODE` (limit to one course), `--headed` (visible browser for debugging).

**Failure behavior:** login failure, layout parse failure (zero courses found, zero rows where the badge implies documents exist), or download errors each produce a notification with the reason and a Playwright screenshot saved to `logs/`. The run continues with remaining courses where possible.

## 8. Module 2: NotebookLM upload (`notebooklm_upload.py`)

**Reality check:** NotebookLM still has no official public consumer API (verified August 2026). Two paths, both shipped in v1, selected by `notebooklm.mode`:

- **`auto` (primary):** use the community `notebooklm-mcp-cli` project (jacob-bd, actively maintained as of May 2026) as a CLI/library. It talks to NotebookLM's internal APIs and supports adding sources to a notebook programmatically. Follow that project's own auth flow (it authenticates against the user's existing Google session). During Milestone 2, verify it supports local file upload for our formats (pdf, docx, audio); if it only handles URLs/text/Drive at that time, pivot the `auto` mode to: sync the file into a dedicated Google Drive folder via `rclone` or the Drive API, then add it as a Drive source through the CLI.
- **`staging` (fallback, always available):** move pending files into `{base_path}/{folder}/_to_upload/`, then send one notification: "N files staged for NotebookLM (Course X, Course Y)". The user drags them in manually (about 20 seconds). Any `auto`-mode failure automatically falls back to staging for that run, so a Google-side breakage never blocks the pipeline.

**Mapping:** notebook per course from `config.courses[].notebook`. A `--list-notebooks` helper prints notebook names/ids on first setup to confirm the mapping. If a mapped notebook is not found: notify, stage the files, continue. Notebook auto-creation is out of scope for v1.

**Dedup:** record `(course_code, filename)` in `state/uploads.json`; never upload the same tuple twice.

**Constraints:** 200 MB per source file; skip larger files with a digest flag. Audio sources are supported, which M4 relies on.

**Accepted risk:** the CLI uses unofficial internal APIs and may break when Google changes them. That is the designed maintenance loop: the failure notification fires, staging mode keeps the pipeline alive, and Claude Code repairs or replaces the integration.

## 9. Module 3: Daily digest (`digest.py`)

**Why this is load-bearing:** the sync clears Omnivox's "new document" indicators as a side effect, removing the user's current forcing function for noticing things. The digest replaces it. The Omnivox mobile app's own push notifications stay ON as an independent safety net.

**Input:** everything M1 observed this run: new documents (with course), new "Quoi de neuf" items, unread MIO subjects/senders, visible due dates. Dedup against `state/announcements.json` by a stable hash of (type, course, title, date).

**Ranking (pluggable `rank(items) -> items` with an `important: bool` field):**

- `rules` (default): an item is important if its title matches any keyword (case/accent-insensitive): `examen, évaluation, test, remise, échéance, reporté, annulé, changement, obligatoire, local, absence, retard`. Keyword list lives in `digest.py` as a constant, easy to extend.
- `gemini` (optional): batch the day's items into one request to Gemini Flash on the free API tier (no cost at this volume; roughly 1 to 2 requests per day), with a strict JSON-only prompt returning `[{id, important, reason}]`. Any API error falls back to `rules` for that run. Free-tier data may be used by Google for training; acceptable here since items are course announcements.

**Output:**

- One notification per run. Format: `"⚠ 1 important · 3 docs (Calcul, Éco) · 2 MIO"` with the important item's title in the body. No important items: plain summary. Nothing new at all: no notification (silence means "checked, nothing new"; the log records the run).
- Append a dated section to `{base_path}/_digest.md` with full detail: every item, links where available, and any pipeline flags (conversion failures, staged uploads, skipped oversized files). This file doubles as the memory any future "ask the hub" agent reads.

## 10. Module 4: Class recorder (`recorder.py`)

Designed now, activated only when `schedule:` is filled (timetable publishes near semester start). Dormant until then.

**Trigger:** `launchd` job `com.school.recorder.plist` runs `recorder.py --tick` every 5 minutes. The tick exits immediately unless the current time falls inside a `schedule` window (with a 10 minute grace after `end`) for a course with `record: true`. This single mechanism handles: on-time starts, arriving late, opening the Mac mid-class, and resuming after a lid-close break, because the next tick after wake restarts capture. `launchd` also fires a missed interval on wake from sleep.

**At-school gate (`at_school_check`):**

- `ip`: `curl -s https://api.ipify.org`, compare against `school_ip_prefix` (captured once by running `recorder.py --capture-ip` while at school). Simple and robust; recommended.
- `ssid`: read current Wi-Fi SSID via `ipconfig getsummary en0`; compare to the cégep SSID. Backup option since macOS increasingly gates SSID access behind location permission.
- `off`: record whenever in a scheduled window (useful for initial testing).
- Gate fails: tick exits silently. No recording off-campus.

**Capture:** `ffmpeg -f avfoundation -i ":0"` (default mic), mono AAC around 64 kbps, **segmented** output (`-f segment -segment_time 300`) into `state/rec_tmp/{date}_{course}/`. A closed lid or sleep kills the current 5 minute chunk at most; everything earlier is already on disk. While recording, hold `caffeinate -i` so the Mac does not idle-sleep with the lid open. Hard limitation, stated plainly: a closed lid means the mic is off; recording cannot continue through sleep. A 2 hour class at this bitrate is roughly 55 to 60 MB, well under NotebookLM's 200 MB cap. If NotebookLM rejects `.m4a` at upload time, transcode the final file to mp3 as part of the finalize step (Milestone 4 verifies this once, then hardcodes the choice).

**Finalize (first tick past `end` + grace):** concatenate chunks with ffmpeg concat into `{base_path}/{folder}/{YYYY-MM-DD} {course_folder} cours.m4a`, delete the temp dir, queue the file through M2, and add a line to the digest ("recorded Calcul, 1h52, uploaded").

**One-time setup:** grant microphone permission to the terminal/ffmpeg on first run; verify with `recorder.py --mic-test` (records 5 seconds and plays it back). A courtesy check the user handles personally: confirm professors are okay with recordings.

## 11. Scheduling (`launchd`)

- `com.school.sync.plist`: `StartCalendarInterval` weekdays at 07:30 and 12:15, running `python -m src.omnivox_sync` with `StandardOutPath`/`StandardErrorPath` into `logs/`. If the Mac is asleep at the scheduled time, launchd fires the job on next wake; if it was powered off, the midday run covers the morning. Install/uninstall via a small `make install-launchd` target that copies plists into `~/Library/LaunchAgents` and runs `launchctl bootstrap`.
- `com.school.recorder.plist`: `StartInterval` 300 seconds. Created in Milestone 4 only.
- Absolute paths everywhere in plists (launchd has no shell environment); Python invoked via the repo's venv absolute path.

## 12. Testing approach

- **Pure logic under pytest:** new-file diffing against state, config validation, digest keyword ranking, dedup hashing, filename formatting, concat ordering. These are the parts worth real tests.
- **Live-site behavior via `--dry-run`:** every module supports it; integration testing against the real LEA and NotebookLM happens in dry-run first, then one supervised live run.
- **`--headed` mode** for visually debugging Playwright flows.
- No recorded HTML fixtures in v1 (YAGNI); the dry-run plus failure screenshots cover it.

## 13. Costs

- Recurring: $0. No paid APIs. Gemini ranking uses the free tier only and is optional.
- Build and maintenance: the user's Claude subscription via Claude Code. Nothing breaks functionally if the subscription is later downgraded; only the optional maintenance convenience changes.

## 14. Known risks and fallbacks

| Risk | Mitigation |
|---|---|
| Omnivox changes page structure | Loud failure notification + screenshot; Claude Code patches selectors. Session-cookie reuse limits login-flow exposure. |
| notebooklm-mcp-cli breaks (unofficial API) | Automatic fallback to staging mode; pipeline never blocks. |
| Mac asleep/off at sync time | launchd wake-fire + second daily run at 12:15. |
| State file corruption/loss | Atomic writes; secondary guard skips files already present on disk. |
| Recording lost to lid close | 5 minute segmentation bounds loss; watchdog resumes on wake. |
| Digest becomes noise | Important-first formatting, silence when nothing is new, keyword list tuned over the semester. |

## 15. Out of scope for v1 (future ideas)

- **Schedule → Google Calendar import** (raised 2026-08-06). Get the cégep
  timetable out of Omnivox/Léa and into Google Calendar. Deliberately *not*
  built as a module here: it runs at most 3 more times (3 semesters left), and
  the hard part is interpreting a genuinely messy schedule presentation, which
  is judgment rather than parsing. Plan is an AI/MCP-driven pass (Google
  Calendar MCP) in the Claude app, with code only as a fallback if that proves
  unreliable. This inverts the repo's "code runs the loop, AI maintains the
  code" rule on purpose: that rule earns its keep for recurring deterministic
  jobs, not for a 3-times-ever interpretation task.
- "Hub" Q&A agent / MCP server over the collected state (the `_digest.md` and JSON state are designed to feed it later).
- Automatic NotebookLM notebook creation; grades scraping; Omnivox calendar-to-ICS export; auto-updating `schedule:` from LEA; per-document AI "is this worth uploading" filtering (v1 uploads everything except oversized files).

## 16. Build milestones (one writing-plans cycle each)

1. **M1: Omnivox sync.** Repo scaffold, `common.py`, config schema, `--discover`, download + convert + state + launchd install. Deliverable: a scheduled dry-run-capable sync that files documents correctly.
2. **M2: NotebookLM upload.** CLI integration (verify file-upload capability, else Drive-source pivot), staging fallback, dedup.
3. **M3: Digest.** Announcement collection in M1's crawl, rules ranking, notification + `_digest.md`, optional Gemini backend.
4. **M4: Recorder.** Built when the fall timetable is published and pasted into `schedule:`.

## 17. Handoff: starting the Claude Code session

```bash
mkdir -p ~/Documents/Programming/school-automation/docs/superpowers/specs
# put this file at:
#   ~/Documents/Programming/school-automation/docs/superpowers/specs/2026-08-06-school-automation-design.md
cd ~/Documents/Programming/school-automation && git init && claude
```

Kickoff prompt to paste into Claude Code:

> Read `docs/superpowers/specs/2026-08-06-school-automation-design.md` in full. The design is already approved. Use the writing-plans skill to create the implementation plan for Milestone 1 only (Omnivox sync, section 7 plus sections 4, 5, 6, 11, 12), save it under `docs/superpowers/plans/`, then implement it plan-first. My Omnivox credentials go in `.env`; ask me to paste them rather than reading them from anywhere. Before writing the plan, run any environment checks you need (Python version, Playwright install, LibreOffice presence via `soffice --version`, brew-install what is missing). Also set up version control: install the GitHub CLI if missing (`brew install gh`), run `gh auth login` and let me complete the browser approval, then create a private GitHub repo named `school-automation` from this folder and push the first commit.

Milestones 2 and 3 follow the same pattern in later sessions. Milestone 4 waits for the timetable.
