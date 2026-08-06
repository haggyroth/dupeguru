# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The tooling that exists to stop a check reporting success on work it never did.

scripts/mutate.py is used to prove a test fails without its fix. Its whole value is that it
refuses to do nothing: a target that does not match -- one wrong space of indentation is
enough -- would otherwise leave the source untouched, the tests passing, and the result
reading as "the test did not catch the mutation" when nothing was mutated.

So the helper needs its own tests, or it is one more thing that can quietly succeed at
nothing. That is the same failure it was written to prevent, one level up.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATE = REPO_ROOT / "scripts" / "mutate.py"


def _run(*args, cwd):
    return subprocess.run([sys.executable, str(MUTATE), *args], capture_output=True, text=True, cwd=cwd)


def test_a_missing_target_is_an_error_not_a_no_op(tmp_path):
    """The failure this tool exists to prevent."""
    target = tmp_path / "sample.py"
    target.write_text("x = 1\n")
    r = _run("apply", str(target), "--old", "not present", "--new", "y", cwd=tmp_path)
    assert r.returncode != 0
    assert "no-op" in r.stderr
    assert target.read_text() == "x = 1\n", "the file was modified despite the error"


def test_a_successful_mutation_changes_the_file_and_can_be_restored(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("threshold = 2\n")

    assert (
        _run("apply", str(target), "--old", "threshold = 2", "--new", "threshold = 999", cwd=tmp_path).returncode == 0
    )
    assert target.read_text() == "threshold = 999\n"

    assert _run("restore", str(target), cwd=tmp_path).returncode == 0
    assert target.read_text() == "threshold = 2\n"


def test_restoring_without_a_backup_is_an_error(tmp_path):
    """Silently doing nothing here would leave a mutation in the tree."""
    target = tmp_path / "sample.py"
    target.write_text("x = 1\n")
    r = _run("restore", str(target), cwd=tmp_path)
    assert r.returncode != 0
    assert "nothing to restore" in r.stderr


def test_mutating_twice_without_restoring_is_refused(tmp_path):
    """Otherwise the second backup overwrites the first and the original is gone."""
    target = tmp_path / "sample.py"
    target.write_text("a\n")
    _run("apply", str(target), "--old", "a", "--new", "b", cwd=tmp_path)
    r = _run("apply", str(target), "--old", "b", "--new", "c", cwd=tmp_path)
    assert r.returncode != 0
    assert "restore before mutating again" in r.stderr


def test_an_unexpected_occurrence_count_is_refused(tmp_path):
    """Replacing three places when you meant one is not the mutation you verified."""
    target = tmp_path / "sample.py"
    target.write_text("v = 1\nv = 1\nv = 1\n")
    r = _run("apply", str(target), "--old", "v = 1", "--new", "v = 2", cwd=tmp_path)
    assert r.returncode != 0
    assert "expected 1" in r.stderr
    assert target.read_text().count("v = 1") == 3
