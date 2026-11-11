#!/usr/bin/env bash
# All-tests runner. Runs every static check + integration test in the repo.
# Exit non-zero if anything fails. Wire into CI as the single entry point.
#
# Usage:
#   ./tests/run_all.sh
#
# Prerequisites (auto-installed via pip/brew if missing):
#   - python3 with PyYAML
#   - ansible-core (ansible-playbook, ansible-galaxy)
#   - ansible-lint, yamllint (pip install)
#   - shellcheck (brew install or apt install)

set -u

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &> /dev/null && pwd)"
cd "${REPO_ROOT}"

fail=0
skipped=0
report=()

# A check that cannot run is recorded as SKIPPED and named in the summary.
# It is neither a pass nor a failure: a silently skipped check is how
# "All checks passed" comes to mean less than it says.
skip() {
    printf '\n=== %s ===\nSKIPPED: %s\n' "$1" "$2"
    report+=("⊘ $1 — SKIPPED: $2")
    skipped=$((skipped+1))
}

step() {
    local name="$1"; shift
    printf '\n=== %s ===\n' "${name}"
    if "$@"; then
        report+=("✓ ${name}")
    else
        report+=("✗ ${name} (exit $?)")
        fail=$((fail+1))
    fi
}

# -- A: YAML syntax sweep -----------------------------------------------------
yaml_sweep() {
    local f bad=0
    while IFS= read -r f; do
        python3 -c "import yaml; yaml.safe_load(open('$f'))" 2>/dev/null \
            || { echo "  ✗ $f"; bad=$((bad+1)); }
    done < <(find . -type f \( -name '*.yml' -o -name '*.yaml' \) \
               -not -path './.git/*' -not -path './payload/*')
    return $bad
}
# PyYAML is a hard requirement for this sweep: without it every file reports
# as broken and the step fails 150+ times for one missing import.
if python3 -c "import yaml" >/dev/null 2>&1; then
    step "YAML syntax (all *.yml / *.yaml)" yaml_sweep
else
    skip "YAML syntax (all *.yml / *.yaml)" \
        "PyYAML not installed — pip3 install PyYAML (ansible-playbook --syntax-check in step E still parses site.yml, plays/ and tests/)"
fi

# -- B: bash syntax sweep -----------------------------------------------------
bash_sweep() {
    local f bad=0
    while IFS= read -r f; do
        bash -n "$f" 2>/dev/null || { echo "  ✗ $f"; bad=$((bad+1)); }
    done < <(find . -type f -name '*.sh' -not -path './.git/*' -not -path './payload/*')
    return $bad
}
step "bash -n on all *.sh" bash_sweep

# -- C: shellcheck (if available) --------------------------------------------
if command -v shellcheck >/dev/null 2>&1; then
    step "shellcheck (scripts/, roles/*/files/, tests/integration/*.sh)" \
        shellcheck scripts/run.sh \
                   roles/nvme_discovery/files/scan_disks.sh \
                   tests/integration/test_run_sh_locale.sh \
                   tests/integration/test_scan_disks.sh \
                   tests/integration/test_python_compat.sh
else
    skip "shellcheck (scripts/, roles/*/files/, tests/integration/*.sh)" \
        "shellcheck not installed — brew install shellcheck / apt install shellcheck"
fi

# -- D: yamllint (if available) ----------------------------------------------
# yamllint is frequently pip-installed without its console script on PATH
# (Homebrew python, --user installs, venvs). Fall back to `python3 -m yamllint`
# rather than silently skipping the step — a skipped check still prints
# "All checks passed" at the end, which reads as coverage we did not have.
YAMLLINT=""
if command -v yamllint >/dev/null 2>&1; then
    YAMLLINT="yamllint"
elif python3 -m yamllint --version >/dev/null 2>&1; then
    YAMLLINT="python3 -m yamllint"
fi

if [[ -n "${YAMLLINT}" ]]; then
    # shellcheck disable=SC2086  # YAMLLINT is a command + optional args
    step "yamllint (plays/, scripts/, tests/, this-session role files)" \
        ${YAMLLINT} plays/ scripts/ tests/integration/ \
                 roles/nvme_discovery/tasks/detect_boot_device.yml \
                 roles/raid_setup/tasks/main.yml \
                 roles/hammerspace_integration/tasks/azure_imds_az.yml \
                 roles/perf_tuning/
else
    skip "yamllint (plays/, scripts/, tests/, this-session role files)" \
        "yamllint not installed — pip3 install yamllint"
fi

# -- E: ansible-playbook --syntax-check --------------------------------------
syntax_sweep() {
    local pb bad=0
    for pb in site.yml plays/*.yml tests/integration/test_*.yml; do
        ansible-playbook --syntax-check "$pb" -i localhost, >/dev/null 2>&1 \
            && echo "  ✓ $pb" || { echo "  ✗ $pb"; bad=$((bad+1)); }
    done
    return $bad
}
step "ansible-playbook --syntax-check (site + plays + tests)" syntax_sweep

# -- F: integration tests (Ansible) ------------------------------------------
integration_sweep() {
    local t bad=0
    for t in tests/integration/test_*.yml; do
        if ansible-playbook "$t" -i localhost, -c local >/tmp/run_all.$$.out 2>&1; then
            grep -E "PASSED \(|FAILED \(" /tmp/run_all.$$.out | head -2 \
                | sed "s|^|  $(basename "$t"): |"
        else
            echo "  ✗ $(basename "$t") failed"
            grep -E "fatal|FAILED" /tmp/run_all.$$.out | head -3 | sed 's|^|    |'
            bad=$((bad+1))
        fi
    done
    rm -f /tmp/run_all.$$.out
    return $bad
}
step "Integration tests (boot-drive, RAID idempotency, protected-split)" integration_sweep

# -- G: locale fallback unit tests -------------------------------------------
step "Locale fallback unit tests (run.sh)" \
    bash tests/integration/test_run_sh_locale.sh

# -- H: protected-disk resolver unit tests -----------------------------------
# Fake-sysfs tests for scan_disks.sh. Runs on macOS too; the "real host scan"
# section inside it self-skips on non-Linux.
step "Protected-disk resolver unit tests (scan_disks.sh)" \
    bash tests/integration/test_scan_disks.sh

# -- I: Python version compatibility -----------------------------------------
# The standalone *.py scripts run under whatever `python3` the SCHEDULER has,
# which is routinely older than the workstation they were written on. Audits
# for 3.10+ constructs in runtime-evaluated positions and smoke-runs each
# script's module body via --help. Added after a PEP 604 annotation
# (`Dict[str, Any] | None`) crashed cleanup_instance_nodes.py on the field
# scheduler while parsing fine everywhere else.
step "Python version compatibility (*.py)" \
    bash tests/integration/test_python_compat.sh

# -- Summary ------------------------------------------------------------------
printf '\n=============================================\n'
printf 'SUMMARY\n'
printf '=============================================\n'
for line in "${report[@]}"; do
    printf '  %s\n' "${line}"
done

if [[ ${fail} -eq 0 ]]; then
    if [[ ${skipped} -gt 0 ]]; then
        printf '\nAll checks that ran passed — %d SKIPPED (see ⊘ above). This is NOT full coverage.\n' "${skipped}"
    else
        printf '\nAll checks passed.\n'
    fi
    exit 0
else
    printf '\n%d check(s) failed' "${fail}"
    [[ ${skipped} -gt 0 ]] && printf ', %d skipped' "${skipped}"
    printf '.\n'
    exit 1
fi
