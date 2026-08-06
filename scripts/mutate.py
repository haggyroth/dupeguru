#!/usr/bin/env python3
# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Apply a temporary source mutation, so a test can be shown to fail without it.

Verifying a fix by reverting it is the habit that catches tests which would pass whether or
not the code under test ran. Doing it with an ad-hoc `str.replace` has a failure mode that
looks exactly like success: if the target text does not match -- one wrong space of
indentation is enough -- the replace silently does nothing, the tests still pass, and that
reads as "the test did not catch the mutation" when nothing was mutated at all. That happened
in #109 and made three sound tests look weak.

This refuses to do nothing. A target that is not found, or found more than once when a single
replacement was asked for, is an error rather than a no-op.

    python scripts/mutate.py apply core/app.py --old "$(cat old.txt)" --new ""
    pytest core/tests/app_test.py -q          # expect a failure
    python scripts/mutate.py restore core/app.py

`apply` saves the original alongside the file so `restore` cannot restore the wrong thing.
"""

import argparse
import shutil
import sys
from pathlib import Path

BACKUP_SUFFIX = ".mutation-backup"


def apply_mutation(path: Path, old: str, new: str, expect: int) -> int:
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if backup.exists():
        print(f"error: {backup} already exists; restore before mutating again", file=sys.stderr)
        return 2

    source = path.read_text()
    found = source.count(old)
    if found == 0:
        print(
            f"error: target not found in {path}. The mutation would have been a no-op, which is\n"
            f"       indistinguishable from a test that failed to catch it. Check indentation.",
            file=sys.stderr,
        )
        return 1
    if expect and found != expect:
        print(f"error: target found {found} time(s) in {path}, expected {expect}", file=sys.stderr)
        return 1

    shutil.copy2(path, backup)
    path.write_text(source.replace(old, new))
    print(f"mutated {path} ({found} replacement(s)); restore with:")
    print(f"    python scripts/mutate.py restore {path}")
    return 0


def restore(path: Path) -> int:
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        print(f"error: no backup at {backup}; nothing to restore", file=sys.stderr)
        return 2
    shutil.move(str(backup), str(path))
    print(f"restored {path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mutate", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("apply", help="replace text, refusing to do nothing")
    a.add_argument("path", type=Path)
    a.add_argument("--old", required=True, help="text to replace; must be present")
    a.add_argument("--new", default="", help="replacement (default: delete)")
    a.add_argument("--expect", type=int, default=1, help="required occurrence count (0 = any)")

    r = sub.add_parser("restore", help="put the original back")
    r.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "apply":
        return apply_mutation(args.path, args.old, args.new, args.expect)
    return restore(args.path)


if __name__ == "__main__":
    sys.exit(main())
