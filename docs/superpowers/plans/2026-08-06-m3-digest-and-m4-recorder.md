# Milestones 3 & 4: Digest and Recorder — Plan and Live Findings

> Built 2026-08-06 in the same session as M1 and M2. This document records the
> design decisions and what the live site actually required, at the level of
> detail that turned out to matter. M1's plan carries the full task-by-task
> format; by this point the shared core, the driver seams and the test
> conventions were established, so these milestones reuse them rather than
> restate them.

---

## Milestone 3 — Daily digest (`src/digest.py`)

**Why it is load-bearing.** M1 clears Omnivox's "new document" indicators as a
side effect of downloading. That removes the user's existing forcing function
for noticing things, so the digest is not a nicety — it is the replacement.
Spec §2 therefore forbids anything the pipeline sees from becoming invisible.

### Collection (added to `omnivox.py`, read-only)

| Source | Selector | Notes |
|---|---|---|
| Quoi de neuf | `#QuoiDeNeufsWrapper a[data-type-service]` | Each card repeats its own label verbatim; an exact doubling is collapsed. `data-type-service` is `MIO` / `FRME` (formulaire) / `DINF` (documents du collège). |
| Events | `div.carte-evenement` | Text reads `"Mardi 2 juin <title>"`. **No year is present**, so `date` is left empty rather than fabricated; the human label goes in `detail`. |
| MIO | `tr.norm` inside the `MioListe.aspx` frame | Unread rows carry `newmsg` in the class. |

**The MIO safety question, settled empirically.** Spec §7 requires reading
subjects *without* opening messages, so unread stays unread. MIO is a frameset
(`MioListeDetailFrameset`) whose panes include a *detail* view, which raised the
risk that merely loading it would mark the first message read. Verified against
a real unread message: after a full frameset load the detail pane shows
"Aucun message sélectionné" and the homepage still reported "1 nouveau Mio".
Collection therefore navigates the frameset, reads the list frame, and **never
clicks a row**.

### Ranking, dedup, output

- `rank_rules` — the spec's keyword list, matched accent- and case-folded, so
  `evaluation` / `Évaluation` / `ÉVALUATION` all hit.
- `rank_gemini` — optional Gemini Flash backend, strict JSON prompt. **Any**
  exception falls back to rules, which is pinned by a test: an API outage must
  not silently lose importance ranking.
- Dedup on `sha1(kind|course|title|date)` in `state/announcements.json`, plus
  within-run dedup so one run cannot report the same item twice.
- One notification per run; **none at all when nothing is new** — silence is the
  signal that the run happened and found nothing.
- `_digest.md` keeps non-important items too, because it doubles as the memory
  a future "ask the hub" agent reads.

Every M3 failure path is contained so it can never fail the sync that downloads
files. Same for M2.

### Verified live
26 portal items collected → 19 new → 4 correctly flagged important
(`Évaluations terminales`, `Délai pour remise des notes`, and a
`Date limite … votre inscription sera annulée`). A second run over identical
input produced 0 items and no notification.

---

## Milestone 4 — Class recorder (`src/recorder.py`)

Built now, **inert** until `schedule:` is populated, so installing the job early
is harmless. `tick()` returns `"inert"` whenever the schedule is empty or no
course has `record: true`, and both are covered by tests.

### Design decisions worth recording

- **One 5-minute tick handles every case** (spec §10): on-time start, arriving
  late, opening the Mac mid-class, resuming after a lid close. There is no
  separate "start" event to miss — the next tick simply resumes.
- **Grace applies to finalizing, not starting.** `active_entry()` deliberately
  excludes the 10-minute grace window; otherwise a tick at `end + 5min` would
  start recording an empty room. `finished_entry()` owns the grace period.
- **Segmented capture** (`-f segment -segment_time 300`) bounds what a lid
  close can destroy to one chunk. `caffeinate -i -w <pid>` holds the machine
  awake only while capture is alive.
- **Chunk ordering is numeric, not lexical**, and sorts by `(start-stamp,
  index)` so a resumed session's chunks land after the first session's — tested,
  because a lexical sort would silently scramble a two-hour lecture.
- **A failed at-school check never records.** Any error in the IP lookup returns
  `False` rather than defaulting to permissive.
- Finalized audio is queued through the **same** `upload_queue.json` M1 writes,
  so M2 uploads recordings with no special-casing.

### Verified
`--mic-test` recorded and played back 5 s of AAC; microphone permission is
already granted. `--status` reports INERT. `--capture-ip` prints a
paste-ready `school_ip_prefix`.

### Outstanding for the user
1. Paste the timetable into `schedule:` when it publishes.
2. Set `record: true` on the courses worth recording.
3. `make install-recorder`.
4. Confirm professors are okay with being recorded — a courtesy call the
   software cannot make.
