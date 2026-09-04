# START HERE

**Copy this entire file and paste it into a fresh AI coding session opened in
this folder.** That is the whole setup process. There is no installer, no
onboarding screen and no interface: this is a personal command-line tool that
somebody built for their own semester, and the AI is what turns it into yours.

Ask the AI anything at any point. It has the code in front of it.

---

## Prompt

You are setting up this project on my machine. Read this whole brief before
running anything, then work through it with me step by step.

### Who I am

I am a cégep student, not a developer. Assume I can copy a command into a
terminal and read what comes back, and assume nothing beyond that. When
something fails, tell me what broke in plain language and what you are doing
about it. Do not hand me a wall of options and ask me to choose; pick the
sensible one, say which you picked, and move on.

### What this project does

Three times a day it logs into Omnivox, opens LÉA, and downloads every document
and assignment brief it has not seen into a folder for that course. It converts
PowerPoint and Word files to PDF along the way, optionally pushes them into a
NotebookLM notebook for that course, and sends one notification summarising
what is new. Silence means it checked and there was nothing.

### What it is not

It is not a product. It has no interface, no account system, no update
mechanism and no error reporting. `config.yaml` is deliberately not in the
repository because it is a personal timetable, so part of your job is building
mine. The NotebookLM upload leans on an unofficial community tool that the
project's own README says will break eventually.

### Before you touch anything, confirm three things with me

1. **My operating system**, exactly. macOS and Windows both work but the paths
   and the scheduler differ, and one of them needs an extra install step.
2. **That I have an Omnivox account at Cégep Édouard-Montpetit.** The portal
   address is hardcoded in three places and the page parsing depends on this
   school's exact French labels. A different cégep means real work, and you
   should tell me that plainly rather than starting.
3. **Python 3.11 or newer.** Run `python3 --version` on macOS or
   `py --version` on Windows and show me the answer.

If any of the three is a no, stop and explain what it would take. Do not
improvise around it.

### Rules you must follow

- **Never ask me for my Omnivox password in the chat, and never type it into a
  file yourself.** When we reach that step, tell me to open `.env` and type it
  in myself, then confirm the file is filled without printing its contents.
  Same for any other password.
- **Never invent a value in `config.yaml`.** Course codes, folder names and
  notebook names come from the discovery step or from me, never from you.
- **After every change, run the offline tests** and tell me the number that
  passed: `pytest -m "not live" -q`. On the machine this came from, 896 pass in
  about 20 seconds. If your number drops, stop and fix it before continuing.
- **Never commit anything** unless I ask. And never run `make public-snapshot`
  or push: those belong to whoever owns this repository, not to me.
- **Prefer showing me the real output** over telling me it worked.

### Order of operations

Do these in order. Do not skip ahead, and check in with me at each numbered
step rather than running the whole thing silently.

**1. Build the environment.**

On macOS: `make venv`. On Windows, `make` does not exist and virtual
environments put Python somewhere else, so run these one at a time instead:

    py -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
    .venv\Scripts\python -m playwright install chromium

The last command downloads a browser, roughly 500 MB. That is expected.

For everything below, on Windows replace `make X` with the underlying command
and `.venv/bin/python` with `.venv\Scripts\python`. Ask me before writing any
`.bat` shortcuts; I probably do not need them.

**2. Install the external tools the README lists.**

`ffmpeg`, and `LibreOffice` for the PowerPoint to PDF conversion. On Windows,
LibreOffice installs to `C:\Program Files\LibreOffice\program\` and does not
add itself to the PATH, so check whether `src/convert.py` can find it and tell
me if it cannot. Skip `whisper.cpp` and the 1.5 GB model entirely for now: that
is only for the lecture recorder, which we are not setting up.

**3. Make my config files.**

    cp config.example.yaml config.yaml
    cp .env.example .env
    cp .private-patterns.example .private-patterns

Then, before anything else, set two things in `config.yaml` for me:
`notebooklm: mode: "staging"` and `notify: macos: false`. I explain why in the
notes at the bottom. Leave everything else alone until after discovery.

Now stop and tell me to fill in `OMNIVOX_USER` and `OMNIVOX_PASS` in `.env`
myself. `OMNIVOX_USER` is my seven-digit student number (DA).

**4. Log in once, interactively.**

    make login

This opens a real browser window. I type my credentials, Omnivox emails me a
six-digit code, and I enter it. **Tell me before I start that I must tick
"J'utilise un appareil de confiance"**, because if I miss it, every scheduled
run afterwards gets challenged and the whole thing quietly stops working.

**5. Discover my courses.**

    make discover

This prints a `courses:` block. Paste it into `config.yaml` for me, then show
me the result and ask whether the folder names look right. They become real
folders on my disk, so this is the moment to change them.

**6. Rehearse, then run for real.**

    make dry-run

Nothing is downloaded and nothing is written. Show me the output and explain
what it would have done. If it looks right, then run `make sync`.

**7. Only after a manual sync works**, ask me whether I want it scheduled.
On macOS that is `make install-launchd`. On Windows there is no equivalent in
this repo and you would set up a Task Scheduler entry pointing at
`.venv\Scripts\python -m src.omnivox_sync`, with "run as soon as possible after
a missed start" ticked and `PYTHONUTF8=1` in the environment.

### Things that fail silently, so watch for them

These do not raise errors. They produce a wrong result quietly, which is worse.

- **NotebookLM notebooks are matched by name and never created.** If a notebook
  named exactly as in `config.yaml` does not already exist, every file goes to
  a staging folder instead and nothing tells you why. This is why we start in
  staging mode.
- **The trusted-device checkbox at step 4.** Missing it does not fail now, it
  fails every night from then on.
- **Accented characters on Windows.** If notebook names come back mangled, the
  name match fails and everything stages. Set `PYTHONUTF8=1`.
- **The at-school gate, if I ever turn on recording.** When it cannot read the
  location or the network it concludes I am not at school and records nothing,
  without complaining. Leave `schedule:` empty and this whole subsystem stays
  inert.

### What we are deliberately not setting up

The lecture recorder and live transcription. It is welded to macOS audio, it is
the largest and most fragile part of the project, and it is completely inert
while `schedule:` is empty in `config.yaml`. If I ask about it later, tell me
what it would cost before starting.

On Windows, folder colours and emoji icons will not work either. That is fine
and needs no action: the code catches its own errors there and logs a warning
per course folder while the sync carries on.

### Notes on the two settings from step 3

**`mode: "staging"`** means the NotebookLM upload tool is never invoked at all.
Each new file is copied into `<course>/_to_upload/` and I drag it into
NotebookLM myself. I keep all the downloading, converting and filing, and lose
only the last drag-and-drop. Once everything else works, we can try switching
to `"auto"`, which needs a separate tool installed and a notebook created by
hand for each course.

**`macos: false`** turns off desktop notifications. If I want notifications on
my phone instead, tell me about the `NTFY_TOPIC` setting in `.env`: I pick any
random string nobody would guess, put it there, and subscribe to the same name
in the ntfy app. Explain that an ntfy topic has no password, so anyone who
knows the name can read my notifications, which is why it belongs in `.env` and
never in `config.yaml`.

### When you are done

Tell me, in five lines or fewer: what runs now, where my files land on disk,
what I still have to do by hand, and the one command I type if I want to sync
right now without waiting.
