# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for run_checked, the fail-closed counterpart to print_and_do.

print_and_do returns an exit code that is silently ignorable, and build steps here ignored
it repeatedly: a missing pyrcc5 produced an empty dg_rc.py and a "build succeeded" (#50),
an uninstaller matched nothing and reported success (#27), a failed makensis exited 0 with
no installer (#63), and build_dmg printed "Build Complete" whether or not hdiutil had
produced anything.

run_checked raises, so ignoring a failure takes an explicit `except` rather than merely
forgetting to look.
"""

import sys

import pytest

from hscommon.build import BuildError, run_checked


def _python(code):
    return f'"{sys.executable}" -c "{code}"'


class TestRunChecked:
    def test_success_is_silent(self):
        run_checked(_python("pass"))

    def test_non_zero_exit_raises(self):
        with pytest.raises(BuildError, match="exit code 3"):
            run_checked(_python("import sys; sys.exit(3)"))

    def test_reports_the_command_in_the_error(self):
        """The message has to say which step failed; build logs are long."""
        with pytest.raises(BuildError, match="sys.exit"):
            run_checked(_python("import sys; sys.exit(1)"))


class TestProducesCheck:
    """The half an exit-code check does not cover: succeeding while writing nothing."""

    def test_missing_artifact_raises_even_on_success(self, tmp_path):
        target = tmp_path / "installer.exe"
        with pytest.raises(BuildError, match="did not create"):
            run_checked(_python("pass"), produces=target)

    def test_empty_artifact_raises(self, tmp_path):
        """Exactly the #50 shape: the file exists, and is zero bytes."""
        target = tmp_path / "dg_rc.py"
        target.write_text("")
        with pytest.raises(BuildError, match="is empty"):
            run_checked(_python("pass"), produces=target)

    def test_written_artifact_passes(self, tmp_path):
        target = tmp_path / "artifact.bin"
        target.write_bytes(b"content")
        run_checked(_python("pass"), produces=target)

    def test_min_size_is_enforced(self, tmp_path):
        target = tmp_path / "artifact.bin"
        target.write_bytes(b"tiny")
        with pytest.raises(BuildError, match="is empty"):
            run_checked(_python("pass"), produces=target, min_size=1024)

    def test_directory_artifact_is_accepted(self, tmp_path):
        """Some steps produce a tree, not a file; size does not apply."""
        target = tmp_path / "dist"
        target.mkdir()
        run_checked(_python("pass"), produces=target)

    def test_failure_takes_precedence_over_the_artifact_check(self, tmp_path):
        """A stale artifact from a previous run must not mask a failing command."""
        target = tmp_path / "artifact.bin"
        target.write_bytes(b"left over from an earlier build")
        with pytest.raises(BuildError, match="exit code"):
            run_checked(_python("import sys; sys.exit(1)"), produces=target)
