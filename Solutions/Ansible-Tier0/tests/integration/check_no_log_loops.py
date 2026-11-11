#!/usr/bin/env python3
"""Audit: no task may loop over the results of a `no_log: true` task.

`no_log: true` suppresses the output of the task that carries it. It does NOT
suppress a downstream task that loops over that task's registered results:
for a looped task Ansible prints the `item`, and when the item is a curl
result it carries

    cmd: curl ... -H "Authorization: Basic <base64 of admin:password>" ...
    invocation.module_args._raw_params: <the same command line>

so a failure in the consumer writes the cluster admin credential to the log
that scripts/run.sh configures.

The fix is to reduce the results to safe fields first -- looping over INDICES
so the `item` is an integer -- and report from a non-looped task.

Usage:  check_no_log_loops.py DIR [DIR...]
Exit 0 and print nothing when clean; exit 1 and print offenders otherwise.
"""
import os
import re
import sys

# `register: X` ... `no_log: true` within the same task block. Task blocks are
# separated by a line starting a new list item at the same indent, so the scan
# is bounded by the next `- name:`.
TASK = re.compile(r"^\s*-\s+name:", re.MULTILINE)
REGISTER = re.compile(r"^\s*register:\s*(\w+)\s*$", re.MULTILINE)
NO_LOG = re.compile(r"^\s*no_log:\s*true\s*$", re.MULTILINE)
LOOP = re.compile(r"^\s*loop:\s*(.+)$", re.MULTILINE)


def censored_vars(body):
    """Names registered by tasks that also declare no_log: true."""
    bounds = [m.start() for m in TASK.finditer(body)] + [len(body)]
    names = set()
    for start, end in zip(bounds, bounds[1:]):
        block = body[start:end]
        reg = REGISTER.search(block)
        if reg and NO_LOG.search(block):
            names.add(reg.group(1))
    return names


def offenders(path):
    with open(path, encoding="utf-8") as fh:
        body = fh.read()

    censored = censored_vars(body)
    if not censored:
        return []

    found = []
    for match in LOOP.finditer(body):
        expr = match.group(1)
        for name in censored:
            # Looping the results directly is the leak. Looping over
            # `range(... | length)` yields integer items and is the fix, so it
            # is explicitly allowed.
            if not re.search(r"\b%s\.results" % re.escape(name), expr):
                continue
            if "range(" in expr:
                continue
            line = body[: match.start()].count("\n") + 1
            found.append(
                "%s:%d: loops over %s.results, registered by a no_log task "
                "— the printed item carries the Authorization header"
                % (path, line, name)
            )
    return found


def main(argv):
    problems = []
    for root in argv[1:]:
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if filename.endswith((".yml", ".yaml")):
                    problems.extend(offenders(os.path.join(dirpath, filename)))
    for line in sorted(problems):
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
