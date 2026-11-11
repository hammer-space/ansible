#!/usr/bin/env python3
"""Audit that every storageVolume.name access is guarded.

Not every entry in a Hammerspace volume group's `locations` list names a
storage volume. Mapping or filtering on 'storageVolume.name' over such an
entry raises

    'dict object' has no attribute 'storageVolume'

and fails a group that is perfectly valid. Each access must therefore sit
downstream of selectattr('storageVolume.name', 'defined') -- or be that
filter itself, or the rejectattr() that isolates the unnameable entries.

Checked per JINJA EXPRESSION rather than per line: these expressions span
several lines, so a line-scoped grep reports false positives on correct code,
and per-FILE presence is too coarse to notice one guard of several going
missing.

Usage:  check_storagevolume_guards.py FILE [FILE...]
Exit 0 and print nothing when clean; exit 1 and print offenders otherwise.
"""
import re
import sys

# {{ ... }} and {% ... %}, non-greedy, across newlines.
EXPR = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
# An access that reads the attribute for its VALUE.
ACCESS = re.compile(
    r"map\(\s*attribute\s*=\s*'storageVolume\.name'"
    r"|(?:select|reject)attr\(\s*'storageVolume\.name'\s*,\s*'(?!defined)"
)
GUARD = re.compile(r"selectattr\(\s*'storageVolume\.name'\s*,\s*'defined'\s*\)")
# rejectattr(..., 'defined') deliberately isolates the unnameable entries.
ISOLATOR = re.compile(r"rejectattr\(\s*'storageVolume\.name'\s*,\s*'defined'\s*\)")


def strip_comments(text):
    """Drop YAML comment lines so prose about the idiom is not audited."""
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in text.splitlines()
    )


def offenders(path):
    with open(path, encoding="utf-8") as fh:
        body = strip_comments(fh.read())

    found = []
    for match in EXPR.finditer(body):
        expr = match.group(0)
        access = ACCESS.search(expr)
        if not access:
            continue
        if ISOLATOR.search(expr):
            continue
        guard = GUARD.search(expr)
        # The guard has to come BEFORE the access, or it filters nothing.
        if guard and guard.start() < access.start():
            continue
        line = body[: match.start()].count("\n") + 1
        found.append(
            "%s:%d: storageVolume.name read with no preceding "
            "selectattr('storageVolume.name', 'defined') in the same expression"
            % (path, line)
        )
    return found


def main(argv):
    problems = []
    for path in argv[1:]:
        problems.extend(offenders(path))
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
