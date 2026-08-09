# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Session-wide checks that run before any test does.

Currently one: a stale compiled extension crashes the interpreter rather than failing a test,
so the run has to say so itself. See scripts/stale_modules.py for why that failure is worth a
guard of its own.

Warn rather than fail. The condition is a local-development state, not a defect in the tree,
and someone may be deliberately testing an older binary. But a warning above a wall of test
output scrolls past unread, so it is repeated in the terminal summary where the run ends.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_configure(config):
    # Imported here rather than at module level: the sys.path entry above has to be in
    # place first, and a module-level import after it is an E402.
    from scripts.stale_modules import stale_report

    config.stash_stale_modules_report = stale_report()
    if config.stash_stale_modules_report:
        # Printed rather than warned: warnings are collected and shown at the end, and this
        # needs to be readable *before* the crash it is warning about.
        print(f"\n{config.stash_stale_modules_report}\n", file=sys.stderr)


def pytest_terminal_summary(terminalreporter):
    report = getattr(terminalreporter.config, "stash_stale_modules_report", None)
    if report:
        terminalreporter.section("stale compiled extensions", sep="!", red=True, bold=True)
        terminalreporter.line(report)
