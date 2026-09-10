# Changelog

Every released version of this project, newest first. Written by hand
at publish time; the development history it comes from is private.

## v13 (2026-09-10)

Setup no longer asks whether your Omnivox is in French or English. It reads that off the public sign-in page, tells you which it found, and if the answer is English it prints exactly what still needs doing. One less question, and one less way to get a silent wrong answer.

## v12 (2026-09-10)

Setting up is faster: the LibreOffice and ffmpeg downloads now run while you are signing in to Omnivox, instead of before it. Neither is needed until the first sync. The README now says what the whole thing costs on disk, measured rather than estimated: about 1.6 GB of tooling plus roughly 100 MB per semester of documents.

## v11 (2026-09-09)

The repo's front page now tells you how to get the code, so handing somebody just the repository link is enough. It also says plainly not to use the Download ZIP button, which cannot receive fixes or send them. If you are catching up an older install, do not rename your folder to match the new project name: the scheduler and the virtual environment hold absolute paths to it.

## v10 (2026-09-09)

MIT licensed now, so you can legally use, change and build on this; before, with no licence at all, technically nobody could. The README lists every feature for the first time, grouped by what it is for, and says which are on by default and how to turn each one off.

## v9 (2026-09-09)

The project is now called omnivox-sync, at github.com/MattDLSJ/omnivox-sync. GitHub redirects the old address, so an existing copy keeps updating and nothing needs to be re-cloned. If you are setting up fresh, use the new URL.

## v8 (2026-09-09)

If you set this up before today, read CATCH-UP.md: it gets an existing copy onto this version without losing whatever your AI had to fix, and turns those fixes into a report. Windows installs no longer open on a wall of failing tests; the ones that only apply to another operating system now skip instead. A copy downloaded as a ZIP repairs itself, and correctly lands on the release it was downloaded from. make setup opens a browser page asking which parts of this you actually want, and make settings has the rest, including when the sync runs. NotebookLM has an off switch now, so opting out no longer leaves a folder of copies in every course. And the privacy guard refuses to certify a report when nothing is armed to check it, instead of passing it as clean.

## v7 (2026-09-09)

Your fixes can now reach everyone else. make report writes up what broke and what you changed, with your OS, Python, college and a diff against the released code filled in already; make send-report checks it for your own private data and opens it as an issue. The sync also updates itself now, at the start of each scheduled run, and refuses to do so if you have your own changes rather than discarding them. Signing in is easier: make login opens a browser, you sign in there, and no password is stored at all unless you want unattended re-login. make find-portal works out your college's Omnivox hostname from its name and verifies it against the live site, so nobody has to read a URL. And a copy downloaded as a ZIP is now detected and repairable: it has no git, so it can never receive a fix or send one.

## v6 (2026-09-08)

Fixes an outage that used to be silent: when Omnivox revokes the trusted-device cookie, the sync stops and every retry asks Omnivox to email another code. It now stops retrying until a person runs make login, drops an _ATTENTION.md next to your course folders explaining the fix, and adds make doctor to answer "is this working" without touching the network.

## v5 (2026-09-04)

A missing .private-patterns blocked every ordinary run of the guard instead of only blocking a release. It now warns and carries on, and only publishing is refused.

## v4 (2026-09-04)

Works with any cégep on Omnivox: the portal hostname and the six French interface labels moved into a school: block in config, so pointing this at another college is a config change rather than a code change. START-HERE now handles a messy mid-semester start, asks which cégep you attend, and ends by telling you what you can actually ask your AI for.

## v3 (2026-09-04)

Security fixes to the private-data guard: exceptions could disarm it, and a missing patterns file degraded it silently. Also fixes a fresh clone being blocked from committing, and make update now explains itself when there is no environment or the history has diverged.

## v2 (2026-09-04)

Documents how to receive updates: make update, or git pull plus a requirements reinstall. CHANGELOG.md lists every version.

## v1 (2026-09-04)

Runs on Windows now: the Unix-only file lock is gone, LibreOffice is findable, filenames survive NTFS, and accented output decodes correctly. Adds START-HERE.md, a paste-into-your-AI setup walkthrough. New: make update pulls the latest release and reinstalls dependencies.
