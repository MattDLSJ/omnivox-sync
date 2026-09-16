#!/bin/sh
# Put this Mac back to "never installed", without touching any coursework.
#
# Shows what it would remove and stops. Pass --yes to actually remove it.
# That default is not politeness: an earlier version of this script was
# tested by filtering its output through sed, the filter only matched
# commands at the start of a line, the deletion was mid-line inside a loop,
# and it removed a working repository during what was supposed to be a
# rehearsal. A script that deletes things should be safe to run by accident.
set -u
GO=0
[ "${1:-}" = "--yes" ] && GO=1

NAMES="omnivox-sync omnivox-sync-main omnivox-sync-master school-automation school-automation-main"
ROOTS="$HOME $HOME/Documents $HOME/Downloads $HOME/Desktop $HOME/Developer $HOME/Projects"

say() { [ "$GO" = 1 ] && echo "$1" || echo "would $1"; }

echo "Looking for an install..."
CANDIDATES=""
for p in "$HOME"/Library/LaunchAgents/com.school.*.plist; do
  [ -e "$p" ] || continue
  wd=$(/usr/libexec/PlistBuddy -c "Print :WorkingDirectory" "$p" 2>/dev/null)
  [ -n "$wd" ] && CANDIDATES="$CANDIDATES
$wd"
done
for r in $ROOTS; do for n in $NAMES; do CANDIDATES="$CANDIDATES
$r/$n"; done; done

REAL=""
for d in $(printf '%s\n' "$CANDIDATES" | sort -u); do
  [ -n "$d" ] || continue
  [ -f "$d/install.py" ] || continue
  case "$d" in
    "$HOME/Documents/School"*|"$HOME/Library/CloudStorage"*)
      echo "  SKIP $d (inside your school files)"; continue ;;
  esac
  if git -C "$d" remote -v 2>/dev/null | grep -q "school-automation"; then
    echo "  STOP. $d is the development repository, not a tester's install."
    echo "        You are on the wrong Mac. Nothing has been touched."
    exit 1
  fi
  echo "  found $d"
  REAL="$REAL $d"
done
[ -n "$REAL" ] || echo "  none (already gone, which is fine)"

echo
for p in "$HOME"/Library/LaunchAgents/com.school.*.plist; do
  [ -e "$p" ] || continue
  label=$(basename "$p" .plist)
  say "stop and remove the scheduled job $label"
  if [ "$GO" = 1 ]; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || launchctl unload "$p" 2>/dev/null
    rm -f "$p"
  fi
done

for d in $REAL; do
  say "delete $d"
  [ "$GO" = 1 ] && rm -rf "$d"
done

[ -d "$HOME/Library/Caches/ms-playwright" ] && {
  say "delete the downloaded browser (about 550 MB)"
  [ "$GO" = 1 ] && rm -rf "$HOME/Library/Caches/ms-playwright"; }
[ -d "$HOME/.notebooklm-mcp-cli" ] && {
  say "delete the NotebookLM sign-in"
  [ "$GO" = 1 ] && rm -rf "$HOME/.notebooklm-mcp-cli" "$HOME/.nlm" "$HOME/.notebooklm-mcp"; }

echo
echo "Never touched: $HOME/Documents/School and everything in it."
[ "$GO" = 1 ] || echo "Nothing was removed. Run it again with --yes to do it."
