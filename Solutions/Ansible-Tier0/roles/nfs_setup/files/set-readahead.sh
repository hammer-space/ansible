#!/usr/bin/env bash
# set-samsung-readahead.sh — safely set readahead=1 MiB for Samsung NVMe namespaces

set -euo pipefail
PATH="/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

RA_KB="${RA_KB:-1024}"                 # 1 MiB (in KiB)
MODEL_REGEX="${MODEL_REGEX:-^SAMSUNG}" # case-insensitive match
DRY_RUN=false
VERBOSE=false

log()  { echo "[$(date +'%F %T')] $*"; }
vlog() { if $VERBOSE; then echo "[$(date +'%F %T')] $*"; fi; }
die()  { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: $0 [--dry-run] [--verbose|-v] [--regex REGEX] [--kb N]
  --dry-run         Show what would change, make no writes (implies --verbose)
  --verbose|-v      Extra logging
  --regex REGEX     Model match regex (default: ${MODEL_REGEX}, case-insensitive)
  --kb N            Readahead in KiB (default: ${RA_KB})
Env overrides: RA_KB, MODEL_REGEX
EOF
}

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --verbose|-v) VERBOSE=true; shift ;;
    --regex) MODEL_REGEX="$2"; shift 2 ;;
    --kb) RA_KB="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown arg: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root."
$DRY_RUN && VERBOSE=true

RA_SECTORS=$(( RA_KB * 2 ))  # blockdev uses 512 B sectors
shopt -s nullglob
NAMESPACES=(/sys/block/nvme*[0-9]n*[0-9])
TOTAL_SYSFS=${#NAMESPACES[@]}
vlog "Discovered ${TOTAL_SYSFS} namespace paths."

MATCHED=0
TOUCHED=0

for path in "${NAMESPACES[@]}"; do
  dev="$(basename "$path")"

  # Only act on plain namespaces (nvme<digits>n<digits>)
  [[ "$dev" =~ ^nvme[0-9]+n[0-9]+$ ]] || { vlog "Skip $dev (pattern mismatch)"; continue; }
  [[ -b "/dev/$dev" ]] || { vlog "Skip $dev (no /dev node)"; continue; }

  model_file="$path/device/model"
  read_kb_file="$path/queue/read_ahead_kb"
  [[ -r "$model_file" ]] || { vlog "Skip $dev (no model)"; continue; }

  model="$(tr -d '\n' < "$model_file" || true)"
  [[ -n "$model" ]] || { vlog "Skip $dev (empty model)"; continue; }

  if echo "$model" | grep -qiE "$MODEL_REGEX"; then
    MATCHED=$((MATCHED+1))
    cur_kb="$(cat "$read_kb_file" 2>/dev/null || echo '?')"

    if $DRY_RUN; then
      log "[DRY] /dev/$dev model='$model' current=${cur_kb} KiB → would set ${RA_KB} KiB (${RA_SECTORS} sectors)"
      continue
    fi

    # Try writing; warn if fails but continue
    if ! echo "$RA_KB" > "$read_kb_file" 2>/dev/null; then
      vlog "WARN: write failed for $read_kb_file ($dev)"
      continue
    fi

    # blockdev optional
    if command -v blockdev >/dev/null 2>&1; then
      if ! blockdev --setra "$RA_SECTORS" "/dev/$dev" 2>/dev/null; then
        vlog "WARN: blockdev --setra failed for /dev/$dev"
      fi
    fi

    new_kb="$(cat "$read_kb_file" 2>/dev/null || echo '?')"
    [[ "$new_kb" == "$RA_KB" ]] || vlog "WARN: verify mismatch on $dev (expected ${RA_KB}, got ${new_kb})"
    TOUCHED=$((TOUCHED+1))
    log "Set readahead: /dev/$dev model='$model' → ${RA_KB} KiB (${RA_SECTORS} sectors)"

    # Mirror to controller paths (nvmeXcYnZ)
    ctrl_prefix="${dev%n*}"
    for cp in /sys/block/${ctrl_prefix}c*n*/queue/read_ahead_kb; do
      [[ -w "$cp" ]] || continue
      echo "$RA_KB" > "$cp" 2>/dev/null || vlog "WARN: mirror write failed for $cp"
      vlog "Mirror: $(dirname "$cp") readahead → ${RA_KB} KiB"
    done
  else
    vlog "Skip /dev/$dev (model '$model' !~ /$MODEL_REGEX/i)"
  fi
done

if $DRY_RUN; then
  log "[DRY] Completed — matched ${MATCHED} namespaces of ${TOTAL_SYSFS}."
else
  log "Completed — updated ${TOUCHED} namespaces (matched ${MATCHED} of ${TOTAL_SYSFS})."
fi

# Summary table
log "Current readahead (nvme namespaces):"
for path in /sys/block/nvme*[0-9]n*[0-9]/queue/read_ahead_kb; do
  dev="${path#/sys/block/}"; dev="${dev%/queue/read_ahead_kb}"
  [[ -b "/dev/$dev" ]] || continue
  echo "$dev $(cat "$path")"
done
