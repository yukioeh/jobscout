#!/bin/bash
# Install or remove the jobscout LaunchAgents.
#
#   ./deploy/schedule.sh install
#   ./deploy/schedule.sh status
#   ./deploy/schedule.sh remove
#
# launchd rather than cron because this runs on a laptop: cron skips
# every hour the machine is asleep and never catches up, while launchd
# fires once after wake if the interval elapsed.
#
# Nothing here sends an application anywhere. run.py emails Eric and
# nobody else.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS=(com.ericharvey.jobscout.hourly com.ericharvey.jobscout.digest com.ericharvey.jobscout.shadow)

case "${1:-}" in
  install)
    mkdir -p "$AGENTS" "$ROOT/data"
    for label in "${LABELS[@]}"; do
      cp "$ROOT/deploy/$label.plist" "$AGENTS/$label.plist"
      launchctl bootout "gui/$UID/$label" 2>/dev/null || true
      launchctl bootstrap "gui/$UID" "$AGENTS/$label.plist"
      echo "loaded $label"
    done
    echo
    echo "hourly poll and the 17:30 digest are scheduled."
    echo "logs: $ROOT/data/run.log"
    ;;

  remove)
    for label in "${LABELS[@]}"; do
      launchctl bootout "gui/$UID/$label" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
      echo "removed $label"
    done
    ;;

  status)
    for label in "${LABELS[@]}"; do
      if launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
        printf '%-38s loaded\n' "$label"
      else
        printf '%-38s not loaded\n' "$label"
      fi
    done
    ;;

  *)
    echo "usage: $0 {install|remove|status}" >&2
    exit 1
    ;;
esac
