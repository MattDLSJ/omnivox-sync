# Catch up an install from before

**For somebody who already set this up and has been running it.** A lot has
changed, and some of it changes how you receive updates at all, so this exists
to get an existing copy onto the current version without losing whatever your
AI had to fix to make it work in the first place.

Hand this whole file to the AI session that set it up, or to a fresh one
opened in the same folder.

---

## Prompt

You set this project up on my machine, or somebody did. It has moved on since.
Work through this in order and do not skip ahead, because the order is the
only thing protecting the changes you made last time.

### The thing to understand before you touch anything

Whatever you patched to get this running is, right now, the single most
valuable thing in the folder. It is a real bug report from a real machine that
the author cannot see, and it exists nowhere else. Every step below is
arranged so that it survives, and so that it reaches him.

So: **do not `git checkout`, do not `git reset --hard`, do not delete and
re-clone.** If you find yourself about to, stop and tell me instead.

There is exactly one exception, the rewritten history in step 3, and it only
applies under the conditions written there. Nowhere else.

### 1. Find out what kind of copy this is

    git status

**If it says "not a git repository"**, this was unzipped rather than cloned.
That is why it has never updated: there is no history to update. Nothing you
have is lost, and the repair touches no file:

    git init
    git remote add origin https://github.com/MattDLSJ/omnivox-sync.git
    git fetch origin
    git reset --mixed origin/main
    git config core.fileMode false

Run `git status` again. What it now lists is the difference between this copy
and the current release. Some of that is my changes and some is just the
releases we missed; the next step sorts that out.

**If it is already a git repository**, carry straight on.

### 2. Save the changes before anything can overwrite them

    git status --porcelain --untracked-files=no

**If that prints nothing**, there is nothing to save. Skip to step 3, and note
for later that this install needed no fixes at all, which is worth reporting
on its own.

**If it prints files**, work out which of them are actually yours. A copy that
is several releases behind will list files it never touched, because the
release changed them. Show me the diff, and put your own work somewhere it
cannot be lost:

    git diff > ../my-changes.patch
    git stash push -m "fixes made during setup"

The patch file is the belt and the stash is the braces. Do not skip either.

### Before step 3, a thing NOT to do: rename the folder

The project is called `omnivox-sync` now. My folder is probably still called
something else, and **that is fine: leave it alone.** The two names have no
reason to match, and renaming the folder breaks real things that hold absolute
paths to it:

- the scheduled job, whose `launchd` plist was written with the old path in it
- the virtual environment, whose scripts have the old path baked into them
- the "Sync School" button, if I have one

None of that is worth a tidier folder name. If I ask you to rename it anyway,
tell me it means running `make venv` and `make install-launchd` again
afterwards, and only do it if I still want to.

### 3. Update

Look before pulling:

    git fetch origin
    git status

**If the fetch printed `(forced update)` beside `main`, or `git status` says
my branch and `origin/main` "have diverged"**, read the next section before
running anything else. The fetch only says "forced update" the first time, so
"diverged" on its own is enough to send you there.

Otherwise:

    git pull --ff-only

If it refuses, tell me exactly what it said rather than forcing anything.

#### If the history was rewritten

On 2026-09-16 the published history was rewritten to remove a file that should
never have shipped. v1 to v32 are unchanged. Every release from v33 on was
rebuilt with the same message and date but a different commit, so a copy that
had pulled any of them now looks to git as if it has commits of its own, and
`git pull --ff-only` refuses with "Not possible to fast-forward". Nothing is
broken.

First confirm that is really what this is. List every commit my copy has that
the release does not:

    git log --format="%h %ad %s" --date=iso origin/main..HEAD

It is the rewrite only if **every** line's subject starts with a version, like
`v33:`, and the same subject with the same date appears in:

    git log --format="%h %ad %s" --date=iso origin/main

Check them all, not the first few. Then make sure nothing is uncommitted:

    git status --porcelain --untracked-files=no

**If every line is an old release and that prints nothing**, this is the one
place where `git reset --hard` is right. Run these one at a time:

    git reset --hard origin/main
    git fetch --force --tags origin

The second moves my version tags onto the rebuilt releases; a plain fetch
leaves them pointing at the old commits. It is safe because a reset only
replaces files git tracks: `.env`, `config.yaml`, `state/` (my signed-in
browser profile) and my course folders are gitignored or outside the
repository, and none of them are touched. Every commit it drops is a release
that exists again, rebuilt, in `origin/main`. Then carry on from
`git pull --ff-only` above, which will have nothing left to do.

**If any line is not an old release**, that commit is mine, or yours from last
time, and it exists nowhere else. Tell me about it, then put it on a branch
before the reset:

    git branch my-changes
    git reset --hard origin/main
    git fetch --force --tags origin

If `git branch` says the name exists, choose another and do not reset until a
branch command has succeeded. The branch keeps those commits exactly as they
were. In step 5, treat them like the stash: bring back only the ones still
needed, with `git cherry-pick` and the commit's hash.

**If the status command printed anything**, step 2 did not save everything. Go
back and do step 2, then return here. Never reset over uncommitted changes.

That is the whole exception: `git reset --hard origin/main`, for this rewrite
only, once every commit it would drop is an old release or already on a
branch, and with nothing uncommitted.

Then reinstall the dependencies, which have changed:

    .venv/bin/python -m pip install -r requirements.txt

On Windows that is `.venv\Scripts\python`, and use PowerShell.

### 4. Check what actually changed

    .venv/bin/python scripts/whats_new.py

Read it out to me. Several of the releases fixed things you may have patched
around by hand, and if the project now fixes one of them properly, your
version should go, not stay.

### 5. Decide, per change, whether to put yours back

    git stash pop

If step 3 put commits of mine on a branch, they count as my changes too:
`git log --oneline origin/main..my-changes` lists them (or whatever name the
branch got).

For each of my changes, tell me plainly: is this still needed, or has the
project fixed it since? Drop the ones that are now redundant. Keep the ones
that are not. If a conflict comes up, explain it in words rather than showing
me a merge marker.

### 6. Set up the parts I actually want

    make setup

A page opens in my browser with three questions. This is new: before, whether
I wanted lecture recording or NotebookLM was guessed rather than asked.
`make settings` has the rest, whenever I want it.

### 7. Fill in the file that keeps my name out of a public issue

This one is mandatory and it is new, so it certainly was not done before.

Open `.private-patterns`. It is gitignored and never leaves this machine. Ask
me for, and write in, one per line:

- my full name, and any short form of it I actually use
- my student number
- the short username that appears in my home folder path
- the name my computer calls itself
- the name of anyone whose device pairs with mine

That file is the only thing that knows those words. The next step publishes
text to a public GitHub issue, and it now refuses to publish at all while that
file is empty, which is the correct behaviour and the reason this step is not
optional.

### 8. Send the report

    make report

It writes up my operating system, my Python, my college, whether the tests
pass, and a diff of anything still different from the release. It leaves a few
questions only you can answer. Answer them from what actually happened during
the original setup: **what broke, what you changed, and whether you think it
belongs upstream.** Use `../my-changes.patch` to remember, and be specific
about errors rather than summarising them.

If the original setup went perfectly and you changed nothing, say exactly
that. "Windows 11, worked first time, no changes" is a genuinely useful
result and takes one minute.

Then:

    make send-report

It checks the report for my private details and refuses to send if it finds
any. It needs no special access: the repository is public and anyone with a
GitHub account can open an issue. If it cannot reach GitHub it prints a link
with the report already filled in, and tells me where the file is.

### 9. Confirm it still works

    make doctor
    make dry-run

Show me the real output of both. Then tell me, in five lines or fewer, what
changed about my install and what I should expect to be different.

### What is different now, so you can tell me

- It **updates itself** at the start of each scheduled sync, and refuses to do
  so if I have my own changes rather than discarding them.
- It **no longer needs my password on disk.** Signing in once in the browser is
  enough. A password only buys unattended re-login.
- A copy downloaded as a ZIP now **repairs itself** instead of being a dead end.
- `make find-portal` works out a college's Omnivox hostname from its name.
- Tests that only apply to another operating system now **skip** rather than
  fail, so a Windows install no longer opens on a wall of red.
