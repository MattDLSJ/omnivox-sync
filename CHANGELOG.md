# Changelog

Every released version of this project, newest first. Written by hand
at publish time; the development history it comes from is private.

## v41 (2026-09-11)

Fixes a bug that stopped the install finishing for anyone: answering the settings page wrote a setting underneath the class timetable, producing a config file nothing could read, and the installer then reported it as "no college is set" and asked you to answer the page again. Windows now gets real notifications instead of silently running a macOS command, and the notifications question appears on the Windows page at all. Course folders are created in the Documents folder your file manager actually shows, which on Windows with OneDrive is not the one built from your home directory. The settings page opens on your own screen even when an AI agent is running the install, and its address is printed immediately rather than held in a buffer until the command exits. The college field suggests as you type and can now spell six colleges it previously could not, including Vanier, Dawson and Maisonneuve. A new install no longer creates a French-named folder holding English-named notebooks. Recording no longer promises three things it does not do, and gym classes are skipped automatically. The installer says when NotebookLM cannot run, shows the files it found before moving them, and reporting works for people other than the author.

## v40 (2026-09-11)

INSTALL.md now opens by saying what the project is and what it is not: Python, one requirement, nothing to compile. An agent that had announced it reviewed the guide went looking for Rust, CMake and Emacs installers, none of which appear anywhere here.

## v39 (2026-09-11)

The link to send somebody now points at INSTALL.md rather than at the repository. Given a repository link, some agents ask what you want done with it and wait, because a repository does not say what it is for. A link to a document is unambiguous, and that document is a page of instructions written for an agent.

## v38 (2026-09-11)

What you send somebody is now a sentence plus the link rather than the link alone. Some agents fetch a bare URL and get to work; others treat it as something you have merely shown them and wait to be told what for, which looks like the project is broken when it is not.

## v37 (2026-09-11)

The calendar step no longer assumes Google. Microsoft, Apple or anything else works the same way, and a cegep Outlook address makes Microsoft at least as likely for a student. The file it writes as a fallback imports into all of them.

## v36 (2026-09-11)

Setting up no longer assumes your AI has a calendar tool. Several do not and cannot add one. It uses one if it is there, and otherwise writes a file you import into any calendar once, rather than stopping to tell you to go and install something.

## v35 (2026-09-11)

The college's own documents, the policies and guides that belong to no course, are now downloaded beside your course folders. Setup also prefers your agent's calendar connector over the old import-a-file route, tells you which account it is about to write to before it writes anything, and knows that a gym class is not worth recording.

## v34 (2026-09-11)

Windows now gets the buttons macOS always had, beside your course folders: one to sync and one to sign in. The sign-in one matters more than it sounds. Windows hides windows opened by background processes, so a sign-in started any other way can open a browser you cannot see, which looks exactly like the program hanging. Double-clicking the file avoids that, and the file says so.

## v33 (2026-09-11)

Setting up now finds the course files already on your machine and files them into the right course, which the settings page had been offering while nothing behind it did anything. A file moves only when its name says which course it belongs to; nothing is deleted, nothing is overwritten, and anything ambiguous is reported and left where it is.

## v32 (2026-09-11)

Setting up now decides instead of recommending. Scheduling, finding the course files you already have, lecture recording and notifications are all questions on the settings page and all arrive switched on, and the installer installs the scheduled job itself rather than telling you how. Reports also file themselves now through a relay, so nobody has to have a GitHub account or click a prefilled link.

## v31 (2026-09-11)

Fixes from the first Windows field report: Python 3.14 on Windows needs tzdata or nothing runs, a test failed on French accents there, document links are matched on their stable id rather than a URL that changes every page load, and the click that precedes a download now gives up after 3 seconds instead of 30. Also one language for the folders this creates: 3_Books, 4_Announcements, _schedule.md, and a term name to match. Existing installs keep the names they have.

## v30 (2026-09-10)

Every course folder now has a 0_README.md telling an AI opened there what the automation is and what it can fetch. The case that prompted it: a session saw an empty book folder, concluded the textbook was unavailable, declined to fetch it on copyright grounds, and rebuilt the answers from slides, while the book was sitting on the student's own paid i+ Interactif account the whole time.

## v29 (2026-09-10)

Fixes MIO saving the wrong message under the right name. Messages were opened by position, and reading one shifts the positions of the rest, so each opened its neighbour's contents: a teacher's name ended up above a stranger's message in that teacher's folder. They are addressed by id now. Also stops opening the whole inbox to keep a handful of it, which was most of the time a sync took.

## v28 (2026-09-10)

MIO can now be saved in full rather than as the inbox preview: set mio: full_bodies: true. It is off by default because there is no way to read a message without opening it, and opening it marks it read, so turning it on means every unread message from a teacher becomes read on the next run. The saved file always says which of the two it is.

## v27 (2026-09-10)

MIO from your own teachers is now saved into that teacher's course folder and uploaded to its notebook, so what they told you in a message is searchable beside their slides. The rest of the inbox is left alone. Nothing opens a message, so nothing is marked read, which means what is saved is the inbox preview and each file says so. This also fixes MIO in the digest, which had been silently missing it because the link it looked for no longer carries the attribute it matched on.

## v26 (2026-09-10)

Teacher communiqués are now saved into each course folder and uploaded to that course's notebook. A communiqué is where instructions like which book to buy, or how to study for the exam, actually live, and nothing in this project had ever read one: they are not a section inside a course, they are a panel on the LEA landing page card, so searching a course for them finds nothing.

## v25 (2026-09-10)

Fixes documents that could never download. A LEA document link expires as soon as another listing for that course is rendered, and reading the assignment briefs renders one, so every document link went stale before it was used: thirty seconds of waiting, then HTTP 404, on every run. Four files had been stuck this way for eight days on the author's own install and all four came through on the first run after the fix.

## v24 (2026-09-10)

Two features nobody could find are now part of the setup. make books pulls chapters out of your i+ Interactif digital textbooks into the course folder, so the textbook ends up in the same notebook as the lecture slides. And the digest can be ranked by an AI instead of keyword rules if you add a Gemini key. Neither was mentioned anywhere an agent would read, so nobody knew they existed.

## v23 (2026-09-10)

NotebookLM notebooks are created for you now, one per course, the first time there is something to put in one. Nothing to set up by hand, so the setup question is just yes or no. If creating one fails, files are copied to a folder as before and nothing is lost. Setting up also asks for your credentials properly at the end if the browser did not hand them back, instead of leaving you to edit a file.

## v22 (2026-09-10)

On Windows the setup no longer asks about lecture recording or desktop notifications, because neither does anything there; it says instead that phone notifications work everywhere and how to switch them on. Your agent should now print the settings page address and stop rather than repeating the questions in chat, where an answer reaches nothing. And the settings page works even when an agent is driving, so only the Omnivox sign-in still needs you at the keyboard.

## v21 (2026-09-10)

Setting up now stops twice instead of three times, and both are browser windows rather than one being a terminal prompt. The settings page asks which college you attend along with the three feature questions, resolves it against the live Omnivox, and only then opens the sign-in. Typing a name it cannot find tells you why and keeps the answers you already gave.

## v20 (2026-09-10)

Fixes a setup that looked like it had hung. Some agent tools run commands in a sandbox, so the Omnivox sign-in opened a browser window the person could never see, while the command waited ten minutes for a sign-in that could not happen. The installer now finishes everything it can, then stops and asks you to run the same command from your own terminal, which picks up exactly where it left off.

## v19 (2026-09-10)

Installing is now one command: python3 install.py on a Mac, py install.py on Windows. It builds the environment, fetches what it needs, finds your college, signs you in, discovers your courses, asks which features you want and runs the first sync. It stops three times, for the three things only you can answer. Safe to run again: every step skips itself if it is already done, so an interrupted install is fixed by running it once more. START-HERE still has the whole sequence by hand, for when something goes wrong.

## v18 (2026-09-10)

Installing no longer asks where to put the folder. It uses your home directory, tells you the path, and carries on, which is one less question in a setup that should have almost none. It still refuses to clone into whatever working directory the agent happens to start in.

## v17 (2026-09-10)

Setting up no longer runs the full test suite after every step. It runs once as a baseline, and again only if a source file actually changes, which takes several minutes of blocking out of a normal install.

## v16 (2026-09-10)

INSTALL.md now says where to put this before cloning it, and make doctor warns if it ended up somewhere temporary. An agent testing the setup cloned the whole project into its own scratch folder, which is where your credentials, your signed-in browser profile and your documents all live, and that folder can be cleared without warning.

## v15 (2026-09-10)

Setting up is one command shorter and one copy-paste lighter: make login now finds your courses and writes them into config.yaml in the same browser session, instead of printing a block for you to paste. Discovery adds what is new and leaves courses you have already set up completely alone, including folder names, teachers and icons. The setup brief now only stops for the three things a person actually has to supply. And the semester name is worked out from the date rather than hardcoded, so an install in January no longer files a winter semester into a folder named after the autumn.

## v14 (2026-09-10)

make login now offers to save your credentials at the end, taken from what you typed into the browser, so you are no longer asked to type your password a second time into another command. Correction to what earlier releases said: those credentials are REQUIRED for scheduled runs, not optional. The stored profile keeps the trusted-device cookie that stops the six-digit codes, but not the signed-in session, which dies with the browser. Measured over 93 runs: 114 sign-ins, zero session reuses. make doctor was calling that setup healthy and now says plainly that nothing scheduled can sign in.

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
