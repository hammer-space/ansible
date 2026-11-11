#!/usr/bin/env bash
# Unit test for the locale-fallback logic in scripts/run.sh.
#
# Verifies:
#   1. Picks the first candidate that exists in `locale -a`.
#   2. Falls through en_US.UTF-8 → C.UTF-8 → en_US.utf8 → C.utf8.
#   3. Errors out cleanly when none are present.
#
# Approach: replays just the picker block with a mocked locale list.
# Independent of the host's real locale state.

set -euo pipefail

passes=0
failures=()

# Helper: run the picker with a mocked `locale -a` output, return the chosen locale.
pick_locale() {
    local mocked_locales="$1"
    local _picked=""
    for cand in en_US.UTF-8 C.UTF-8 en_US.utf8 C.utf8; do
        if printf '%s\n' "${mocked_locales}" | grep -qiE "^${cand}$"; then
            _picked="${cand}"
            break
        fi
    done
    echo "${_picked}"
}

run_case() {
    local name="$1"; shift
    local mock="$1"; shift
    local expected="$1"; shift
    local got
    got="$(pick_locale "${mock}")"
    if [[ "${got}" == "${expected}" ]]; then
        passes=$((passes+1))
        printf '  ✓ %s\n' "${name}"
    else
        failures+=("${name}: expected '${expected}', got '${got}'")
        printf '  ✗ %s\n' "${name}"
    fi
}

printf '%s\n' "RUN.SH locale fallback unit tests"
printf '%s\n' "================================="

# 1. Normal host — has en_US.UTF-8
run_case "en_US.UTF-8 present → picks it first" \
    "en_US.UTF-8
C.UTF-8
ko_KR.UTF-8
POSIX" \
    "en_US.UTF-8"

# 2. en_US.UTF-8 missing, C.UTF-8 present (typical minimal container)
run_case "no en_US.UTF-8 → falls back to C.UTF-8" \
    "C
C.UTF-8
POSIX" \
    "C.UTF-8"

# 3. Lowercase variants (some distros)
run_case "lowercase en_US.utf8 → picks it after upper-case misses" \
    "C
en_US.utf8
ko_KR.utf8" \
    "en_US.utf8"

# 4. Only C.utf8 (lowercase, some old systems)
run_case "only C.utf8 → picks it last in chain" \
    "C
POSIX
C.utf8" \
    "C.utf8"

# 5. Pure Korean host (real-world Keith scenario)
run_case "ko_KR-only host → returns empty (forces error path)" \
    "C
POSIX
ko_KR.UTF-8" \
    ""

# 6. Empty locale list (broken libc install)
run_case "empty locale list → returns empty" \
    "" \
    ""

# 7. Case insensitivity: en_us.utf-8 should still match en_US.UTF-8 (grep -i)
run_case "en_us.utf-8 lowercase variant matches en_US.UTF-8" \
    "en_us.utf-8" \
    "en_US.UTF-8"

# 8. Multiple candidates available — picks first in priority order
run_case "all four available → picks en_US.UTF-8 (highest priority)" \
    "C.UTF-8
en_US.UTF-8
en_US.utf8
C.utf8" \
    "en_US.UTF-8"

# ============================================================================
# ANSIBLE_LOG_PATH default behavior
# ============================================================================

echo
printf '%s\n' "RUN.SH ANSIBLE_LOG_PATH default tests"
printf '%s\n' "====================================="

run_log_case() {
    local name="$1"; shift
    local preset="$1"; shift
    local custom_dir="$1"; shift
    local expected_dir="$1"; shift

    # `[[ ... ]] && export` returns the LHS's exit code when LHS is false,
    # which under `set -e` aborts the script. Use `if` blocks instead.
    unset ANSIBLE_LOG_PATH ANSIBLE_LOG_DIR
    if [[ -n "${preset}" ]];     then export ANSIBLE_LOG_PATH="${preset}";     fi
    if [[ -n "${custom_dir}" ]]; then export ANSIBLE_LOG_DIR="${custom_dir}"; fi

    if [[ -z "${ANSIBLE_LOG_PATH:-}" ]]; then
        _log_dir="${ANSIBLE_LOG_DIR:-${HOME}/logs}"
        export ANSIBLE_LOG_PATH="${_log_dir}/ansible-stub.log"
    fi

    local got_dir
    got_dir="$(dirname "${ANSIBLE_LOG_PATH}")"

    if [[ "${got_dir}" == "${expected_dir}" ]]; then
        passes=$((passes+1))
        printf '  ✓ %s\n' "${name}"
    else
        failures+=("${name}: expected dir '${expected_dir}', got '${got_dir}'")
        printf '  ✗ %s\n' "${name}"
    fi

    unset ANSIBLE_LOG_PATH ANSIBLE_LOG_DIR
}

# 1. no preset → default to ~/logs
run_log_case "no preset → ~/logs/" \
    "" "" "${HOME}/logs"

# 2. ANSIBLE_LOG_DIR override → that dir
run_log_case "ANSIBLE_LOG_DIR=/tmp/x → /tmp/x" \
    "" "/tmp/x" "/tmp/x"

# 3. user-set ANSIBLE_LOG_PATH wins over the default
run_log_case "user-set ANSIBLE_LOG_PATH preserved" \
    "/some/custom/path.log" "" "/some/custom"

# 4. user-set ANSIBLE_LOG_PATH wins even when ANSIBLE_LOG_DIR is also set
run_log_case "ANSIBLE_LOG_PATH overrides ANSIBLE_LOG_DIR" \
    "/abs/log.log" "/ignored" "/abs"

echo
echo "================================="
echo "PASSED: ${passes}"
if [[ ${#failures[@]} -eq 0 ]]; then
    echo "FAILED: 0"
    exit 0
fi
echo "FAILED: ${#failures[@]}"
for f in "${failures[@]}"; do
    echo "  - ${f}"
done
exit 1
