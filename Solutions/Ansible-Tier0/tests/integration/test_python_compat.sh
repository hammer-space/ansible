#!/usr/bin/env bash
#
# Python version-compatibility tests for the repo's standalone scripts.
#
# 2026-09-03 field failure. decommission_tier0.yml runs
# cleanup_instance_nodes.py with `python3` on the CycleCloud scheduler, and it
# died before doing any work:
#
#   File ".../cleanup_instance_nodes.py", line 45, in <module>
#     class HammerspaceClient:
#   File ".../cleanup_instance_nodes.py", line 127, in HammerspaceClient
#     def get_storage_volume(self, volume_name: str) -> Dict[str, Any] | None:
#   TypeError: unsupported operand type(s) for |: '_GenericAlias' and 'NoneType'
#
# `X | Y` in an annotation (PEP 604) is valid SYNTAX everywhere, so the file
# compiles and every linter is happy — but on Python < 3.10 the expression is
# EVALUATED when the def is executed, and typing.Dict does not implement `|`.
# The scheduler runs an older interpreter than the workstation the script was
# written on, so this only ever appeared in the field.
#
# Two checks, because neither alone is sufficient:
#
#   1. An AST audit. Catches the construct even in code paths no smoke test
#      reaches, and distinguishes annotations that are evaluated at runtime
#      from ones deferred by `from __future__ import annotations`.
#
#   2. A --help smoke test. Actually EXECUTES each module body and class body,
#      which is exactly where this failure lives. A syntax check would not
#      have caught it.
#
# Minimum supported interpreter is Python 3.9: RHEL 9 and Ubuntu 20.04 both
# ship it, and the scheduler in the field report predates 3.10.
#
# Usage:  bash tests/integration/test_python_compat.sh

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}" || exit 1

pass=0
fail=0

printf '\n--- AST audit: constructs that need Python 3.10+ ---\n'

audit_out="$(python3 - <<'PY'
import ast
import os
import sys

MIN = (3, 9)
problems = []


def scan(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        problems.append("%s:%s: does not parse: %s" % (path, exc.lineno, exc.msg))
        return

    # `from __future__ import annotations` defers evaluation (PEP 563), so
    # `X | Y` in an annotation is only a string and never executed.
    deferred = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )

    annotations = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                annotations.append((node.lineno, node.returns))
            args = node.args
            for arg in list(args.args) + list(args.kwonlyargs) + list(
                getattr(args, "posonlyargs", [])
            ):
                if arg.annotation:
                    annotations.append((arg.lineno, arg.annotation))
        elif isinstance(node, ast.AnnAssign) and node.annotation:
            annotations.append((node.lineno, node.annotation))
        elif node.__class__.__name__ == "Match":
            problems.append(
                "%s:%d: match/case needs Python 3.10+" % (path, node.lineno)
            )

    if deferred:
        return
    for lineno, ann in annotations:
        for sub in ast.walk(ann):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                problems.append(
                    "%s:%d: PEP 604 'X | Y' annotation is evaluated at runtime "
                    "and raises TypeError on Python < 3.10 — use "
                    "Optional[...]/Union[...], or add "
                    "'from __future__ import annotations'" % (path, lineno)
                )
                break


for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".ansible")]
    for name in sorted(files):
        if name.endswith(".py"):
            scan(os.path.join(root, name))

for line in problems:
    print(line)
sys.exit(1 if problems else 0)
PY
)"
audit_rc=$?

if [[ ${audit_rc} -eq 0 ]]; then
    printf '  ✓ no Python 3.10+ constructs in runtime-evaluated positions\n'
    pass=$((pass + 1))
else
    printf '  ✗ Python 3.10+ constructs found:\n'
    printf '%s\n' "${audit_out}" | sed 's/^/      /'
    fail=$((fail + 1))
fi

printf '\n--- smoke test: each script'"'"'s module body executes ---\n'

# --help makes argparse exit 0 after the module and class bodies have run,
# which is where the field failure lived. No API call is made.
shopt -s nullglob
for script in "${repo_root}"/*.py; do
    name="$(basename "${script}")"
    if out="$(python3 "${script}" --help 2>&1)"; then
        printf '  ✓ %s\n' "${name}"
        pass=$((pass + 1))
    elif grep -Eqi 'ModuleNotFoundError|ImportError|No module named|not installed|pip3? install' <<< "${out}"; then
        # An optional third-party SDK missing on this machine is an
        # environment gap, not a compatibility defect. Skipped rather than
        # failed — but note the pattern is deliberately narrow: a TypeError
        # from a PEP 604 annotation does NOT match it and still fails.
        printf '  – %s skipped (optional dependency not installed here)\n' "${name}"
    else
        printf '  ✗ %s failed to run --help\n' "${name}"
        printf '%s\n' "${out}" | tail -12 | sed 's/^/      /'
        fail=$((fail + 1))
    fi
done
shopt -u nullglob

printf '\n=============================================\n'
printf 'python_compat: %d passed, %d failed\n' "${pass}" "${fail}"
printf '=============================================\n'
[[ ${fail} -eq 0 ]]
