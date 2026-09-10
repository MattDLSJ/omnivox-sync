PY := $(CURDIR)/.venv/bin/python

.PHONY: books button capture-ip capture-location check-private discover doctor dry-run fetch-vad field-notes find-portal ics install-hooks install-launchd install-live install-manual install-recorder install-retry invite login mic-test mirror public-snapshot publish record-now recorder-status report send-report setup-cheneliere setup-gemini setup-ics setup-mic setup-omnivox shortcuts sound-check sync test test-unit uninstall-launchd uninstall-live uninstall-manual uninstall-recorder uninstall-retry update upload venv

venv:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	$(PY) -m playwright install chromium

test:
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest -m "not live"

# Fail if anything personal is in the repo, or is about to be. Needles come
# from .env, config.yaml and .private-patterns, none of which are tracked, so
# this script knows your values without ever containing them.
#   make check-private            the tracked tree
#   make check-private STAGED=1   only what is staged, which is what the hook runs
#   make check-private HISTORY=1  also the commit messages and author lines
# Is this actually working? Answers without touching the network, and tells
# you what needs a person. Run it when something feels off.
doctor:
	@$(PY) -m src.omnivox_sync --doctor

check-private:
	$(PY) scripts/check_private.py $(if $(STAGED),--staged) $(if $(HISTORY),--history)

# Refuse any commit that would carry something personal in. One-time setup.
# core.hooksPath is local config and is never cloned, so every checkout needs
# this once. It refuses to pretend: with no .private-patterns there is nothing
# to look for, and a guard with nothing to look for guards nothing.
install-hooks:
	@test -s .private-patterns || { \
	  echo "No .private-patterns. Copy the example and fill it in first:"; \
	  echo "  cp .private-patterns.example .private-patterns"; \
	  exit 1; }
	git config core.hooksPath .githooks
	@echo "pre-commit and commit-msg hooks active. Bypass one commit with --no-verify."

# Build a shareable copy: the tree at HEAD, one commit, an author you choose,
# no history. Use this ONCE, to create the public repository. After that,
# `make publish` ships updates to it.
public-snapshot:
	$(PY) scripts/public_snapshot.py

# Ship an update to the public repo: the tree at HEAD, one new commit on top
# of what is already published, a version tag and a changelog entry. Your
# private history is never read. Anyone downstream just runs `make update`.
#   make publish MESSAGE="what changed"
# MESSAGE travels through the ENVIRONMENT, never interpolated into this
# recipe. Through the shell, quotes were silently dropped, a $ ate the rest of
# the word, and backticks executed: the published changelog would not have said
# what you typed. Leave MESSAGE out and it asks you, which handles accents,
# quotes and two lines correctly.
publish:
	@PUBLISH_MESSAGE=$${MESSAGE:-} $(PY) scripts/publish.py \
	  $(if $(MESSAGE),--message-env PUBLISH_MESSAGE) $(if $(DRY),--dry-run)

# For someone who INSTALLED this: pull the latest release and reinstall
# anything new. Dependencies do change between releases, and a pull on its own
# leaves you with the new code and the old packages, which fails confusingly.
update:
	@test -x $(PY) || { \
	  echo "No Python environment here yet. Run this first:"; \
	  echo "  make venv"; \
	  exit 1; }
	@git pull --ff-only || { \
	  echo ""; \
	  echo "Could not fast-forward. Your copy has commits or edits the"; \
	  echo "release does not. Put them aside and try again:"; \
	  echo "  git stash          # then: make update, then: git stash pop"; \
	  echo "or keep them on a branch:"; \
	  echo "  git switch -c my-changes && git switch - && make update"; \
	  exit 1; }
	$(PY) -m pip install -q -r requirements.txt
	@$(PY) -m pytest -m "not live" || \
	  echo "Tests fail after updating. That is worth reporting: make report"
	@$(PY) scripts/whats_new.py

# One public link to hand somebody, holding INSTALL.md and nothing else. The
# repository stays private; only the instructions are public, and they give
# nothing away because getting the code still needs access. Creates the gist
# the first time, updates it every time after, and the link never changes.
#   make invite          create or update
#   make invite NEW=1    start a new gist, if the old one was deleted
invite:
	@$(PY) scripts/invite.py $(if $(NEW),--new)

# Which <something>.omnivox.ca belongs to your college? Asks for the name in
# plain words and then VERIFIES the answer against the live site, so a wrong
# guess cannot be mistaken for a right one. Falls back to telling you to read
# your address bar, which is always correct and never fails.
find-portal:
	@$(PY) -m src.portal_finder

# Your copy hits things this one never will: another college's portal, another
# operating system, another language. A fix that stays on your laptop helps
# one person, so this is the road back.
#   make report          write it up; most of it fills itself in
#   make report FORCE=1  throw away the one you started and begin again
#   make send-report     check it for YOUR private data, then open an issue
report:
	@$(PY) scripts/field_report.py $(if $(FORCE),--force) $(if $(NOTESTS),--no-tests)

send-report:
	@$(PY) scripts/field_report.py --send $(if $(DRY),--dry-run)

# The other side of `make report`: everything anyone has sent, in one file,
# ready to hand to an AI along with "work out what of this belongs upstream".
field-notes:
	@$(PY) scripts/field_report.py --collect

dry-run:
	$(PY) -m src.omnivox_sync --dry-run

discover:
	$(PY) -m src.omnivox_sync --discover

login:
	$(PY) -m src.omnivox_sync --login

sync:
	$(PY) -m src.omnivox_sync

upload:
	$(PY) -m src.notebooklm_upload

mic-test:
	$(PY) -m src.recorder --mic-test

ics:
	$(PY) -m src.calendar_export

# Refresh each course's _horaire.md from the LIVE calendar, so NotebookLM and
# any agent pointed at the folder can see the content and the notes that only
# exist in Google Calendar. Runs on its own inside every sync; this target is
# for doing it on demand. Needs SCHOOL_ICS_URL in .env, and is inert without it.
mirror:
	$(PY) -m src.calendar_mirror

# Put a 1_NotebookLM.webloc at the top of every course folder: one double
# click from the folder you are working in to the notebook that holds the same
# material. The leading digit is what makes Finder sort it above the documents.
# Idempotent, so it is safe to re-run; it only writes when a link changed.
shortcuts:
	$(PY) -m src.shortcuts

# Interactive credential setup. Run the target as printed: it asks, you paste,
# it checks the value before writing it. Nothing to substitute into a command,
# and the value never goes through the shell, so it stays out of the history.
setup-ics:
	@$(PY) scripts/set_env.py SCHOOL_ICS_URL

setup-omnivox:
	@$(PY) scripts/set_env.py OMNIVOX_USER
	@$(PY) scripts/set_env.py OMNIVOX_PASS

# Grant the microphone to the recorder. There is nothing to switch on in
# System Settings until macOS has asked, and macOS refuses to ask when python
# is the responsible process, so this builds a signed .app around ffmpeg and
# runs it from launchd. Run it as printed; it asks, you click Autoriser, it
# verifies there is real sound in what it captured. See src/micapp.py.
setup-mic:
	@$(PY) scripts/setup_mic.py

# The i+ Interactif textbooks. `make books` lists what the account owns; then
# work a chapter at a time, which is both what fair dealing covers and what
# fits under NotebookLM's 200 MB cap:
#   .venv/bin/python -m src.iplus --book "Histoire du monde" --chapters
#   .venv/bin/python -m src.iplus --book "Histoire du monde" --chapter 4
books:
	@$(PY) -m src.iplus --list

# Chenelière / i+ Interactif, for the digital textbooks. Run it as printed:
# it asks, you paste, nothing is substituted into a command and the password
# never goes through the shell history.
setup-cheneliere:
	@$(PY) scripts/set_env.py CHENELIERE_USER
	@$(PY) scripts/set_env.py CHENELIERE_PASS

setup-gemini:
	@$(PY) scripts/set_env.py GEMINI_API_KEY

# Record the class happening right now, ignoring the at-school gate. For when
# a class has started, the Mac cannot tell it is on campus, and debugging that
# from your seat is not the priority.
record-now:
	@$(PY) -m src.recorder --record-now

capture-location:
	$(PY) -m src.recorder --capture-location

capture-ip:
	$(PY) -m src.recorder --capture-ip

recorder-status:
	$(PY) -m src.recorder --status

# One install/uninstall pair per launchd label. GNU make keeps the LAST recipe
# for a duplicated target and only warns, so a stray duplicate here silently
# inverts a target: `make install-recorder` used to uninstall the recorder.
# tests/test_makefile.py now fails on any duplicate.
install-launchd:
	./scripts/install_launchd.sh install

uninstall-launchd:
	./scripts/install_launchd.sh uninstall

install-retry:
	LABEL_OVERRIDE=com.school.retry ./scripts/install_launchd.sh install

uninstall-retry:
	LABEL_OVERRIDE=com.school.retry ./scripts/install_launchd.sh uninstall

install-recorder:
	LABEL_OVERRIDE=com.school.recorder ./scripts/install_launchd.sh install

uninstall-recorder:
	LABEL_OVERRIDE=com.school.recorder ./scripts/install_launchd.sh uninstall

# Live transcription, every 60 s. Separate from the recorder tick on purpose:
# that one asks CoreLocation where the Mac is and can block for 25 s, which is
# far too heavy a thing to do every minute. This job returns immediately unless
# a capture is already running.
install-live:
	LABEL_OVERRIDE=com.school.live ./scripts/install_launchd.sh install

uninstall-live:
	LABEL_OVERRIDE=com.school.live ./scripts/install_launchd.sh uninstall

# The button's on-demand job. Dormant until kickstarted; never scheduled.
install-manual:
	LABEL_OVERRIDE=com.school.manual ./scripts/install_launchd.sh install

uninstall-manual:
	LABEL_OVERRIDE=com.school.manual ./scripts/install_launchd.sh uninstall

# Builds "Sync School.app" beside the course folders, so it sits where the
# school work is rather than in ~/Applications. The location comes from
# base_path in config.yaml, so it follows the folder each semester.
# Needs install-manual first.
button:
	./scripts/make_button_app.sh "$$($(PY) -c 'import sys; sys.path.insert(0,"."); from pathlib import Path; from src.common import load_config; print(load_config(Path("config.yaml")).base_path)')/Sync School.app"

fetch-vad:  ## Download the Silero VAD model (864 KB) into ~/.cache/whisper
	@mkdir -p $(HOME)/.cache/whisper
	@if [ -f "$(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin" ]; then \
		echo "Already present: $(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin"; \
	else \
		echo "Fetching Silero VAD from ggml-org/whisper-vad ..."; \
		curl -sL --fail -o "$(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin.part" \
		  "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin" \
		  && mv "$(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin.part" \
		        "$(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin" \
		  && echo "OK: $$(ls -lh $(HOME)/.cache/whisper/ggml-silero-v5.1.2.bin | awk '{print $$5}')"; \
	fi

sound-check:  ## Record from your mic and hear the untreated vs treated version
	$(PY) -m src.recorder --sound-check
