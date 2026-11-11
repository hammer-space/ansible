#!/usr/bin/env bash
# Wrapper for ansible-playbook / ansible-inventory that forces a UTF-8 locale.
#
# Background: Ansible refuses to start when the system locale isn't UTF-8 (the
# Korean ko_KR incident on dskbd079). This wrapper finds a UTF-8 locale that is
# actually installed on the host and exports it before invoking ansible.
#
# Tries (in order): en_US.UTF-8, C.UTF-8, en_US.utf8, C.utf8. If none is
# present, prints a clear instruction for generating one and exits non-zero
# instead of letting ansible bomb with a cryptic error.
#
# Usage:
#   ./scripts/run.sh playbook site.yml
#   ./scripts/run.sh playbook site.yml -e deploy_di=true --tags di
#   ./scripts/run.sh inventory -i inventory.yml --list
#   ./scripts/run.sh raw ansible-playbook site.yml      # pass any ansible-* command

set -euo pipefail

# locale -a output normalizes casing — match case-insensitively.
_available_locales="$(locale -a 2>/dev/null || true)"
_picked=""
for cand in en_US.UTF-8 C.UTF-8 en_US.utf8 C.utf8; do
    if printf '%s\n' "${_available_locales}" | grep -qiE "^${cand}$"; then
        _picked="${cand}"
        break
    fi
done

if [[ -z "${_picked}" ]]; then
    cat >&2 <<EOF
ERROR: no UTF-8 locale is installed on this host. ansible cannot start.

Generate one:
  # Debian / Ubuntu
  sudo locale-gen en_US.UTF-8
  # RHEL / Rocky / Alma
  sudo dnf install -y glibc-langpack-en
  # Or fall back to C.UTF-8 (universal):
  sudo localedef -i en_US -f UTF-8 en_US.UTF-8

Available locales on this host:
${_available_locales:-  (none)}
EOF
    exit 1
fi

export LANG="${_picked}"
export LC_ALL="${_picked}"
export LANGUAGE="${_picked}"

# ANSIBLE_LOG_PATH: shell aliases (e.g. `alias ansible-playbook='ANSIBLE_LOG_PATH=... ansible-playbook'`
# in .bashrc) do NOT propagate through `exec` in this script. Set a sensible
# default here so every invocation gets logged. User's pre-set value wins.
if [[ -z "${ANSIBLE_LOG_PATH:-}" ]]; then
    _log_dir="${ANSIBLE_LOG_DIR:-${HOME}/logs}"
    mkdir -p "${_log_dir}" 2>/dev/null || true
    # Declare-then-export (SC2155): $(...) failure must not be hidden by export's exit code.
    _log_file="${_log_dir}/ansible-$(date +%Y%m%d_%H%M%S).log"
    export ANSIBLE_LOG_PATH="${_log_file}"
fi
echo "[run.sh] locale=${_picked}  ANSIBLE_LOG_PATH=${ANSIBLE_LOG_PATH}" >&2

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &> /dev/null && pwd)"
cd "${REPO_ROOT}"

if [[ $# -lt 1 ]]; then
    cat <<EOF
Usage: $0 <subcommand> [args...]

Subcommands:
  playbook   <args>   -> ansible-playbook <args>
  inventory  <args>   -> ansible-inventory <args>
  vault      <args>   -> ansible-vault <args>
  raw        <cmd>    -> exec <cmd> (full ansible-* command)

Active locale     : ${_picked}
ANSIBLE_LOG_PATH  : ${ANSIBLE_LOG_PATH}

Tip: override the log path with ANSIBLE_LOG_PATH=/your/path ./scripts/run.sh ...
     or change the directory with ANSIBLE_LOG_DIR=/your/dir ./scripts/run.sh ...
EOF
    exit 1
fi

sub="$1"
shift

case "${sub}" in
    playbook)   exec ansible-playbook "$@" ;;
    inventory)  exec ansible-inventory "$@" ;;
    vault)      exec ansible-vault "$@" ;;
    raw)        exec "$@" ;;
    *)
        echo "Unknown subcommand: ${sub}" >&2
        exit 2
        ;;
esac
