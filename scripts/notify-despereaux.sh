#!/usr/bin/env sh
# Chaptarr / Readarr-style "Custom Script" notifier for despereaux.
#
# Wire-up:
#   In chaptarr (or Readarr): Settings → Connect → "+ Custom Script"
#   Path: /scripts/notify-despereaux.sh   (mount this file into the chaptarr container)
#   Triggers: "On Import" (and "On Upgrade" if available)
#
# Inputs (Readarr env-var convention — chaptarr likely inherits):
#   $readarr_eventtype           = "Download" | "Test" | "Grab" | "Rename" | …
#   $readarr_addedbookpaths      = pipe-delimited absolute paths of newly imported files
#   $readarr_book_path           = book folder (some events)
#
# Required env (set in chaptarr's container OR in the script body):
#   DESPEREAUX_URL    e.g. http://despereaux:8000   (Docker network DNS name)
#   DESPEREAUX_TOKEN  same value as DESPEREAUX_WEBHOOK_TOKEN on despereaux
#   MOUNT_TRANSLATE   optional: rewrite paths from chaptarr's mount to despereaux's
#                     (e.g. /books -> /ebooks). Leave unset if both see /ebooks.
#
# Exit codes: always 0 (logging only) so a flaky webhook never blocks an import.

set -u

URL="${DESPEREAUX_URL:-http://despereaux:8000}"
TOKEN="${DESPEREAUX_TOKEN:-}"
TRANSLATE_FROM="${MOUNT_TRANSLATE_FROM:-/books}"
TRANSLATE_TO="${MOUNT_TRANSLATE_TO:-/ebooks}"

log() { printf '[notify-despereaux] %s\n' "$*" >&2; }

if [ -z "$TOKEN" ]; then
  log "DESPEREAUX_TOKEN unset — skipping (set it in chaptarr container env)"
  exit 0
fi

# Skip non-import events.
case "${readarr_eventtype:-}" in
  Test)
    log "Test event — pinging /healthz"
    curl -fsS -m 5 "${URL%/}/healthz" >/dev/null 2>&1 \
      && log "healthz ok" \
      || log "healthz unreachable at $URL"
    exit 0
    ;;
  Download|Upgrade|Rename|Import)
    : # proceed
    ;;
  "")
    log "no readarr_eventtype set — assuming import"
    ;;
  *)
    log "ignoring event: ${readarr_eventtype}"
    exit 0
    ;;
esac

paths_raw="${readarr_addedbookpaths:-}"
if [ -z "$paths_raw" ] && [ -n "${readarr_book_path:-}" ]; then
  paths_raw="${readarr_book_path}"
fi

if [ -z "$paths_raw" ]; then
  log "no paths in event payload — falling back to full scan"
  curl -fsS -m 10 -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"paths":[]}' \
    "${URL%/}/api/admin/sync" \
    && log "full scan queued" \
    || log "sync POST failed"
  exit 0
fi

# Build JSON array of paths, translating mount points if configured.
json_paths=""
old_IFS="$IFS"
IFS='|'
for p in $paths_raw; do
  IFS="$old_IFS"
  # Mount-point rewrite: chaptarr likely sees /books, despereaux sees /ebooks.
  case "$p" in
    "${TRANSLATE_FROM}"*) p="${TRANSLATE_TO}${p#${TRANSLATE_FROM}}" ;;
  esac
  # JSON-escape: only worry about backslash + double-quote (paths shouldn't have control chars).
  esc=$(printf '%s' "$p" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  if [ -z "$json_paths" ]; then
    json_paths="\"${esc}\""
  else
    json_paths="${json_paths},\"${esc}\""
  fi
  IFS='|'
done
IFS="$old_IFS"

body="{\"paths\":[${json_paths}]}"
log "POST $URL/api/admin/sync with $(printf '%s' "$paths_raw" | tr '|' '\n' | wc -l | tr -d ' ') path(s)"
curl -fsS -m 10 -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$body" \
  "${URL%/}/api/admin/sync" \
  && log "sync queued" \
  || log "sync POST failed"
exit 0
