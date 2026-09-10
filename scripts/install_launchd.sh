#!/usr/bin/env bash
# Render and (un)install the com.school.sync launchd job.
# Absolute paths only: launchd jobs run with no shell environment.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${LABEL_OVERRIDE:-com.school.sync}"
TEMPLATE="$REPO/launchd/$LABEL.plist.template"
RENDERED="$REPO/launchd/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$REPO/.venv/bin/python"
DOMAIN="gui/$(id -u)"

usage() { echo "usage: [LABEL_OVERRIDE=com.school.recorder] $0 {install|uninstall|status}" >&2; exit 64; }

install_job() {
  [ -x "$PYTHON" ] || { echo "No venv at $PYTHON. Run: make venv" >&2; exit 1; }
  [ -f "$TEMPLATE" ] || { echo "Missing template: $TEMPLATE" >&2; exit 1; }

  mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"
  # Python, not sed: the sync schedule comes from config.yaml now, and it is
  # a block of XML rather than a word. See scripts/render_plist.py.
  "$PYTHON" "$REPO/scripts/render_plist.py" "$LABEL" > "$RENDERED"
  plutil -lint "$RENDERED" >/dev/null
  cp "$RENDERED" "$TARGET"

  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$TARGET"
  launchctl enable "$DOMAIN/$LABEL"
  echo "Installed $LABEL."
  echo "Run it once now with: launchctl kickstart -p $DOMAIN/$LABEL"
}

uninstall_job() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$TARGET" "$RENDERED"
  echo "Uninstalled $LABEL."
}

case "${1:-}" in
  install)   install_job ;;
  uninstall) uninstall_job ;;
  status)    launchctl print "$DOMAIN/$LABEL" 2>/dev/null || echo "$LABEL is not loaded." ;;
  *)         usage ;;
esac
