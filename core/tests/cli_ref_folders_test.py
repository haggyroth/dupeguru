# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""``--ref`` actually protects the folder it names (issue #162).

The flag was applied only to folders that were *also* passed positionally, so the common form
-- naming a subfolder of the path being scanned -- silently did nothing and its files were
marked and deleted like any other duplicate. Existence was still validated, so a typo errored
cleanly; only a correct invocation failed, and it failed quietly.

The tests that matter most are the ones that delete real files. ``is_ref_folder`` in the output
is a useful signal, but it is a report about the guarantee rather than the guarantee itself: the
thing worth pinning is that a file inside a reference folder is still on disk afterwards.

The old form, with the folder given both positionally and as ``--ref``, is covered too. It was
the only form that ever worked, so it is the one most likely to be broken by fixing the others.
"""

import json

import pytest

from cli import EXIT_BAD_ARGS, EXIT_DUPES_FOUND, main
from core.confidence import Confidence


def write(directory, files):
    for name, content in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def plan_of(argv, capsys):
    assert main(argv) == EXIT_DUPES_FOUND
    return json.loads(capsys.readouterr().out)


def would_delete(payload):
    return {dupe["path"] for entry in payload["plan"] for dupe in entry["duplicates"] if dupe["would_delete"]}


@pytest.fixture
def nested(tmp_path):
    """Two identical files in a folder to protect, one more in a folder to clean."""
    write(tmp_path / "keep", {"a.txt": b"same", "b.txt": b"same"})
    write(tmp_path / "copies", {"c.txt": b"same"})
    return tmp_path


class TestASubfolderOfTheScannedPath:
    """The form that was broken, and the one people will reach for first."""

    def test_files_in_the_reference_folder_are_not_planned_for_deletion(self, nested, capsys):
        payload = plan_of([str(nested), "--ref", str(nested / "keep"), "--plan"], capsys)
        doomed = would_delete(payload)
        assert doomed, "nothing was planned at all, so this proves nothing about what was spared"
        assert not any("/keep/" in path or "\\keep\\" in path for path in doomed)

    def test_the_duplicate_outside_it_is_still_planned(self, nested, capsys):
        # The protection must not turn into "delete nothing"; the point is still to clean up.
        payload = plan_of([str(nested), "--ref", str(nested / "keep"), "--plan"], capsys)
        assert any(path.endswith("c.txt") for path in would_delete(payload))

    def test_the_files_are_reported_as_being_in_a_reference_folder(self, nested, capsys):
        assert main([str(nested), "--ref", str(nested / "keep")]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        members = [payload["groups"][0]["reference"]] + payload["groups"][0]["duplicates"]
        in_keep = [m for m in members if "keep" in m["path"]]
        assert in_keep, "fixture did not put a file from keep/ into the group"
        assert all(m["is_ref_folder"] for m in in_keep)

    def test_a_deletion_really_leaves_them_on_disk(self, nested, capsys):
        # The guarantee itself rather than a report about it. is_ref_folder could be right while
        # the deleter still removed the file.
        assert main([str(nested), "--ref", str(nested / "keep"), "--delete", "--yes"]) == EXIT_DUPES_FOUND
        survivors = sorted(p.name for p in (nested / "keep").iterdir())
        assert survivors == ["a.txt", "b.txt"], "a file in the reference folder was deleted"

    def test_the_group_reads_as_corroborated(self, nested, capsys):
        # Knock-on from #124: a Reference-folder member is one of the two corroborating signals,
        # so while --ref was being dropped these groups were under-reported as content-only.
        payload = plan_of([str(nested), "--ref", str(nested / "keep"), "--plan"], capsys)
        assert payload["plan"][0]["confidence"] == Confidence.CORROBORATED
        assert "Reference" in payload["plan"][0]["confidence_reason"]


class TestAFolderOutsideTheScan:
    """``--ref`` promises its files are scanned, so naming an unscanned folder scans it."""

    def test_it_is_scanned_rather_than_ignored(self, tmp_path, capsys):
        write(tmp_path / "copies", {"c.txt": b"same"})
        write(tmp_path / "originals", {"orig.txt": b"same"})
        payload = plan_of([str(tmp_path / "copies"), "--ref", str(tmp_path / "originals"), "--plan"], capsys)
        assert len(payload["plan"]) == 1, "the reference folder was not scanned, so nothing matched"

    def test_its_own_file_is_kept_and_the_copy_goes(self, tmp_path, capsys):
        write(tmp_path / "copies", {"c.txt": b"same"})
        write(tmp_path / "originals", {"orig.txt": b"same"})
        payload = plan_of([str(tmp_path / "copies"), "--ref", str(tmp_path / "originals"), "--plan"], capsys)
        doomed = would_delete(payload)
        assert all(path.endswith("c.txt") for path in doomed)
        assert doomed, "the copy should still be planned for deletion"


class TestTheFormThatAlreadyWorked:
    """Given both positionally and as --ref. The only form that ever worked, so the likeliest
    casualty of fixing the others."""

    def test_it_still_protects_the_folder(self, tmp_path, capsys):
        write(tmp_path / "keep", {"a.txt": b"same"})
        write(tmp_path / "copies", {"c.txt": b"same"})
        argv = [str(tmp_path / "keep"), str(tmp_path / "copies"), "--ref", str(tmp_path / "keep"), "--plan"]
        payload = plan_of(argv, capsys)
        doomed = would_delete(payload)
        assert doomed
        assert all(path.endswith("c.txt") for path in doomed)


class TestSeveralReferenceFolders:
    def test_each_one_is_applied(self, tmp_path, capsys):
        write(tmp_path / "keep_a", {"one.txt": b"same"})
        write(tmp_path / "keep_b", {"two.txt": b"same"})
        write(tmp_path / "copies", {"three.txt": b"same"})
        argv = [
            str(tmp_path),
            "--ref",
            str(tmp_path / "keep_a"),
            "--ref",
            str(tmp_path / "keep_b"),
            "--plan",
        ]
        doomed = would_delete(plan_of(argv, capsys))
        assert doomed, "nothing was planned, so this proves nothing"
        assert all(path.endswith("three.txt") for path in doomed)

    def test_a_reference_folder_containing_a_scanned_one_still_applies(self, tmp_path, capsys):
        # add_path drops any scanned folder underneath the one being added, so this is the case
        # where the order the set happened to iterate in could have changed the result.
        write(tmp_path / "outer" / "inner", {"a.txt": b"same"})
        write(tmp_path / "copies", {"c.txt": b"same"})
        argv = [
            str(tmp_path / "outer" / "inner"),
            str(tmp_path / "copies"),
            "--ref",
            str(tmp_path / "outer"),
            "--plan",
        ]
        doomed = would_delete(plan_of(argv, capsys))
        assert doomed
        assert all(path.endswith("c.txt") for path in doomed), "a file under the reference folder was planned"

    @pytest.mark.parametrize("swap", [False, True])
    def test_nested_reference_folders_agree_whichever_order_they_are_given(self, tmp_path, capsys, swap):
        # Not a correctness knob -- set_state returns early when the state already matches, and
        # get_state walks parents, so both orders converge. Pinned because it is the case where
        # add_path's habit of dropping folders underneath the one being added could have made
        # the outcome depend on the iteration order of a set.
        write(tmp_path / "data" / "sub", {"a.txt": b"same"})
        write(tmp_path / "data", {"b.txt": b"same"})
        write(tmp_path, {"other.txt": b"same"})
        refs = [tmp_path / "data", tmp_path / "data" / "sub"]
        if swap:
            refs.reverse()
        argv = [str(tmp_path), "--ref", str(refs[0]), "--ref", str(refs[1]), "--plan"]
        doomed = would_delete(plan_of(argv, capsys))
        assert doomed
        assert all(path.endswith("other.txt") for path in doomed)


class TestBadInput:
    def test_a_missing_reference_folder_still_errors(self, tmp_path, capsys):
        # This validation was already right; the bug was that passing it made no difference
        # afterwards. Keeping it pinned so the fix does not turn a clean error into a silent add.
        write(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        assert main([str(tmp_path), "--ref", str(tmp_path / "nope")]) == EXIT_BAD_ARGS
        assert "reference folder does not exist" in capsys.readouterr().err
