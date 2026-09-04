#!/usr/bin/env bash
# Builds "Sync School.app", a Dock button that runs one sync.
#
# The destination is passed in; `make button` derives it from base_path in
# config.yaml so the button sits beside the course folders and follows them
# when the semester rolls over.
#
# The bundle is generated rather than committed so it stays reviewable: the
# whole app is the fifteen lines of shell below, and it can be rebuilt or
# inspected at any time.
#
# All the app does is ask launchd to run com.school.manual once. It does not
# build a command line, does not know where the venv is, and never touches
# ~/Documents itself. That matters for three reasons:
#
#   * launchd refuses to start a second instance of a label already running,
#     so an impatient double-click is a no-op rather than a second browser
#     against the same Omnivox profile.
#   * The job runs with the same identity as the three agents that already run
#     daily, so macOS asks for no new permission.
#   * There is exactly one copy of the command, in the plist. A button that
#     re-declared it would drift the first time the venv moved.
set -euo pipefail

APP="${1:?usage: make_button_app.sh <path/to/Sync School.app>}"
LABEL="com.school.manual"

if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "The $LABEL job is not installed yet." >&2
  echo "Run this first:  make install-manual" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Sync School</string>
    <key>CFBundleDisplayName</key>     <string>Sync School</string>
    <key>CFBundleIdentifier</key>      <string>ca.schoolautomation.syncschool</string>
    <key>CFBundleExecutable</key>      <string>syncschool</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.0</string>
    <!-- No window, no menu bar, no bouncing second icon in the switcher.
         Clicking it fires the job and the app exits immediately. -->
    <key>LSUIElement</key>             <true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/syncschool" <<'SH'
#!/bin/sh
# Ask launchd to run the sync once. Feedback comes from the job's own
# notifications, so there is nothing to report here except a missing job.
TARGET="gui/$(id -u)/com.school.manual"

if ! launchctl kickstart "$TARGET" >/dev/null 2>&1; then
  osascript -e 'display dialog "The school sync job is not installed.

Open Terminal in the school-automation folder and run:
    make install-manual" buttons {"OK"} default button 1 with icon caution giving up after 30' >/dev/null 2>&1
  exit 1
fi
SH

chmod +x "$APP/Contents/MacOS/syncschool"

# Nudge Finder/Dock to notice a freshly written bundle.
touch "$APP"

# Wear the same green "School" tag as the course folders it sits beside, so it
# does not look like a stray download in the middle of the semester folder.
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PYTHONPATH="$REPO_DIR" "$REPO_DIR/.venv/bin/python" - "$APP" <<'TAG' >/dev/null 2>&1 || true
import sys
from pathlib import Path
from src.finder import set_finder_tag, set_color_label
set_finder_tag(Path(sys.argv[1]), "School", "green")
set_color_label(Path(sys.argv[1]), "green")
TAG
fi

echo "Built: $APP"
echo
echo
echo "To put it in the Dock:"
echo "  open \"$(dirname "$APP")\"   then drag \"Sync School\" onto the Dock"
echo
echo "Pressing it runs one sync: Omnivox -> download -> convert -> NotebookLM -> digest."
echo "It notifies when it starts and when it ends, including when nothing is new."
