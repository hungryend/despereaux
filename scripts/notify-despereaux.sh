#!/usr/bin/env sh
# Chaptarr / Readarr-style "Custom Script" notifier for despereaux.
#
# Wire-up: chaptarr/Readarr → Settings → Connect → "+ Custom Script"
#   Path: /scripts/notify-despereaux.sh   Triggers: On Import / On Upgrade / On Rename
# Required env on the chaptarr container:
#   DESPEREAUX_URL    (e.g. http://despereaux:8000)
#   DESPEREAUX_TOKEN  (= DESPEREAUX_WEBHOOK_TOKEN on despereaux)
#
# On any import/upgrade/rename it POSTs a full-library sync to despereaux, which
# ingests new/changed files (despereaux's own fs-watcher handles deletes/renames).
# A full scan is cheap for this library and avoids fragile per-path JSON building.
# Exit codes: always 0 (logging only) so a flaky webhook never blocks an import.
set -u

URL="${DESPEREAUX_URL:-http://despereaux:8000}"
TOKEN="${DESPEREAUX_TOKEN:-}"

log() { printf '[notify-despereaux] %s\n' "$*" >&2; }

# Portable HTTP — *arr/LSIO images commonly ship wget, not curl.
if command -v curl >/dev/null 2>&1; then
  http_get()  { curl -fsS -m "$1" "$2" >/dev/null 2>&1; }
  http_post() { curl -fsS -m "$1" -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -d "$3" "$2" >/dev/null 2>&1; }
elif command -v wget >/dev/null 2>&1; then
  http_get()  { wget -q -T "$1" -O /dev/null "$2"; }
  http_post() { wget -q -T "$1" -O /dev/null --header="Authorization: Bearer ${TOKEN}" --header="Content-Type: application/json" --post-data="$3" "$2"; }
else
  http_get()  { log "neither curl nor wget present"; return 1; }
  http_post() { log "neither curl nor wget present"; return 1; }
fi

if [ -z "$TOKEN" ]; then
  log "DESPEREAUX_TOKEN unset — skipping (set it in chaptarr container env)"
  exit 0
fi

case "${readarr_eventtype:-}" in
  Test)
    log "Test event — pinging /healthz"
    http_get 5 "${URL%/}/healthz" && log "healthz ok" || log "healthz unreachable at $URL"
    exit 0
    ;;
  Download|Upgrade|Rename|Import|"") : ;;
  *) log "ignoring event: ${readarr_eventtype}"; exit 0 ;;
esac

log "import event (${readarr_eventtype:-none}) — queuing despereaux full scan"
http_post 10 "${URL%/}/api/admin/sync" '{"paths":[]}' \
  && log "full scan queued" \
  || log "sync POST failed"
exit 0
