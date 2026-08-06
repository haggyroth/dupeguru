#!/usr/bin/env python3
# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Summarise `mutmut run`, separating survivors worth acting on from equivalent mutants.

A raw survivor list is close to unusable here. On the cache modules, mutmut generates 319
mutants and 131 survive -- but 83 of those only change the case of a SQL keyword
("PRAGMA" -> "pragma"), which SQLite treats identically, and most of the rest replace an
argument to logging.debug. Reading 131 lines to find the four that matter is how a tool like
this stops being used.

This groups them:

  equivalent   only the inside of a string literal changed; behaviour is identical
  diagnostic   a logging or print argument; wrong, but invisible to users
  behaviour    everything else -- read these

`behaviour` is a shortlist, not a verdict. Some of those are equivalent too, and deciding
takes a person. The point is that the shortlist is short.

    python scripts/mutants_report.py
"""

import re
import subprocess
import sys

MUTMUT = ["mutmut"]


def _run(args):
    return subprocess.run(MUTMUT + args, capture_output=True, text=True).stdout


def _blank_strings(text: str) -> str:
    """Replace every string literal with a placeholder, so only structure is compared."""
    return re.sub(r"\"[^\"]*\"|'[^']*'", "<S>", text)


def _classify(diff: str):
    removed = [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    added = [ln[1:].strip() for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    if not removed or not added:
        return "behaviour", "", ""
    before, after = removed[0].lstrip("+- "), added[0].lstrip("+- ")
    if _blank_strings(before) == _blank_strings(after):
        return "equivalent", before, after
    if "logging." in before or "print(" in before:
        return "diagnostic", before, after
    return "behaviour", before, after


def main() -> int:
    results = _run(["results"])
    if not results.strip():
        print("no mutmut results; run `make mutants` first", file=sys.stderr)
        return 2
    survivors = [ln.strip().split(":")[0] for ln in results.splitlines() if ln.strip().endswith(": survived")]
    # `mutmut results` lists only mutants that were *not* killed, so the kill count is not
    # derivable here. `mutmut run` prints it; this deliberately does not invent one.
    untested = sum(1 for ln in results.splitlines() if ln.strip().endswith(": no tests"))

    buckets = {"equivalent": [], "diagnostic": [], "behaviour": []}
    for name in survivors:
        kind, before, after = _classify(_run(["show", name]))
        buckets[kind].append((name, before, after))

    print(f"  {len(survivors)} survived, {untested} had no covering test")
    print("  (kill count is printed by `mutmut run`; it is not in `mutmut results`)")
    print(f"    equivalent (string-only, ignore) : {len(buckets['equivalent'])}")
    print(f"    diagnostic (logging, low value)  : {len(buckets['diagnostic'])}")
    print(f"    behaviour  (read these)          : {len(buckets['behaviour'])}")
    if buckets["behaviour"]:
        print()
        for name, before, after in buckets["behaviour"]:
            print(f"    {name.split('ǁ')[-1]}")
            print(f"        - {before[:96]}")
            print(f"        + {after[:96]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
