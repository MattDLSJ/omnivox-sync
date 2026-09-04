# Changelog

Every released version of this project, newest first. Written by hand
at publish time; the development history it comes from is private.

## v3 (2026-09-04)

Security fixes to the private-data guard: exceptions could disarm it, and a missing patterns file degraded it silently. Also fixes a fresh clone being blocked from committing, and make update now explains itself when there is no environment or the history has diverged.

## v2 (2026-09-04)

Documents how to receive updates: make update, or git pull plus a requirements reinstall. CHANGELOG.md lists every version.

## v1 (2026-09-04)

Runs on Windows now: the Unix-only file lock is gone, LibreOffice is findable, filenames survive NTFS, and accented output decodes correctly. Adds START-HERE.md, a paste-into-your-AI setup walkthrough. New: make update pulls the latest release and reinstalls dependencies.
