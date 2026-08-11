# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Comparing the bytes before deleting, when asked (issue #188).

Everything upstream reasons about digests, and a digest is a claim about content rather than the
content. On the xxhash path that claim is worth about 10^-27 of doubt; on the md5 fallback it
was worth much less, because md5 collisions are constructible -- see #189.

This closes the gap by reading both files and comparing them at the moment of deletion. Two
properties matter more than the mechanism:

- it must **refuse** a file whose bytes differ, and say so, rather than deleting it anyway;
- it must **not** refuse where byte-identity was never claimed. A picture match says two images
  look alike; a re-encode scores 100% while the files genuinely differ. Byte-comparing those
  would refuse every deletion in picture mode and make the option a trap.

The forged pair from #189 is used as the adversarial case, because it is the one where every
digest-based check in the application agrees the files are the same and only a byte comparison
disagrees.
"""

import pytest

import cli
from core.app import AppMode, DeleteStatus, DupeGuru
from core.deletion_plan import claims_byte_identity, verify_identical
from core.engine import Group, Match, MatchKind
from core.scanner import ScanType
from core.tests.hash_algorithm_test import MD5_COLLISION_A, MD5_COLLISION_B


class _File:
    """Just enough of a File to be compared, grouped and deleted.

    size and mtime are read from disk rather than invented, because check_deletable compares
    them against the filesystem immediately before deleting -- a made-up value would be refused
    as "changed since the scan" and the test would pass for the wrong reason.
    """

    def __init__(self, path):
        self.path = path
        self.name = path.name
        self.is_ref = False
        self.words = []
        try:
            stat = path.stat()
            self.size, self.mtime = stat.st_size, stat.st_mtime
        except OSError:
            self.size, self.mtime = 0, 0

    def exists(self):
        return self.path.exists()


def written(tmp_path, name, data):
    target = tmp_path / name
    target.write_bytes(data)
    return _File(target)


class TestTheComparison:
    def test_identical_files_verify(self, tmp_path):
        a = written(tmp_path, "a", b"same content")
        b = written(tmp_path, "b", b"same content")
        assert verify_identical(a, b) is True

    def test_different_contents_do_not(self, tmp_path):
        a = written(tmp_path, "a", b"one thing")
        b = written(tmp_path, "b", b"another!!")
        assert verify_identical(a, b) is False

    def test_different_sizes_do_not(self, tmp_path):
        a = written(tmp_path, "a", b"short")
        b = written(tmp_path, "b", b"considerably longer")
        assert verify_identical(a, b) is False

    def test_empty_files_verify(self, tmp_path):
        assert verify_identical(written(tmp_path, "a", b""), written(tmp_path, "b", b"")) is True

    def test_a_difference_past_the_first_chunk_is_found(self, tmp_path):
        # Reading in chunks is what keeps this affordable; stopping at the first chunk would
        # make it a prefix check wearing the name of a full comparison.
        from core.deletion_plan import _COMPARE_CHUNK

        head = b"x" * (_COMPARE_CHUNK + 512)
        a = written(tmp_path, "a", head + b"A")
        b = written(tmp_path, "b", head + b"B")
        assert verify_identical(a, b) is False

    def test_a_file_that_cannot_be_read_is_not_verified(self, tmp_path):
        # A refusal, not an exception: something unreadable cannot be shown to be a duplicate,
        # and the caller's job is to decline rather than crash.
        a = written(tmp_path, "a", b"data")
        missing = _File(tmp_path / "not-here")
        assert verify_identical(a, missing) is False

    def test_the_forged_pair_is_rejected(self, tmp_path):
        # Two different files that md5 cannot tell apart. Every digest-based check in the
        # application would call these duplicates; this is the one that does not.
        a = written(tmp_path, "a", MD5_COLLISION_A)
        b = written(tmp_path, "b", MD5_COLLISION_B)
        assert verify_identical(a, b) is False


class TestWhatItAppliesTo:
    def test_an_exact_match_claims_byte_identity(self, tmp_path):
        a = written(tmp_path, "a", b"data")
        b = written(tmp_path, "b", b"data")
        group = Group()
        group.add_match(Match(a, b, 100, kind=MatchKind.EXACT))
        assert claims_byte_identity(group, group.dupes[0]) is True

    @pytest.mark.parametrize("kind", [MatchKind.RESEMBLANCE, MatchKind.METADATA])
    def test_other_kinds_do_not(self, tmp_path, kind):
        # The trap this avoids: verifying a resemblance would refuse every deletion in picture
        # mode, since a resized copy is meant to differ.
        a = written(tmp_path, "a", b"one")
        b = written(tmp_path, "b", b"two")
        group = Group()
        group.add_match(Match(a, b, 100, kind=kind))
        assert claims_byte_identity(group, group.dupes[0]) is False

    def test_a_missing_group_claims_nothing(self):
        assert claims_byte_identity(None, object()) is False


@pytest.fixture
def scanned(tmp_path):
    """A real app holding a real content-matched group."""

    def build(files):
        for name, content in files.items():
            (tmp_path / name).write_bytes(content)
        app = DupeGuru(view=cli._HeadlessView())
        app.app_mode = AppMode.STANDARD
        app.options["scan_type"] = ScanType.CONTENTS
        app.directories.add_path(tmp_path)
        cli._run_scan(app, verbose=False)
        return app

    return build


class TestTheDeletionRefuses:
    def test_a_collision_is_grouped_but_not_deleted(self, scanned, tmp_path):
        # The whole point, end to end.
        #
        # The group is built by hand rather than scanned, because the active hash correctly
        # separates the forged pair -- so a scan produces no group and the test would skip,
        # which is the one outcome that proves nothing. What a collision *does* is put two
        # differing files in one group under an EXACT match, and that is constructed directly
        # here. It is exactly the state a broken or forged hash leaves behind.
        app = scanned({"a.bin": MD5_COLLISION_A, "b.bin": MD5_COLLISION_B})
        first, second = (_File(tmp_path / name) for name in ("a.bin", "b.bin"))
        group = Group()
        group.add_match(Match(first, second, 100, kind=MatchKind.EXACT))
        app.results.groups = [group]
        app.options["verify_before_delete"] = True

        dupe = group.dupes[0]
        with pytest.raises(OSError) as caught:
            app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert "same contents" in str(caught.value)
        assert dupe.path.exists(), "the file was deleted despite failing verification"

    def test_without_verification_that_same_collision_is_deleted(self, scanned, tmp_path):
        # The counterfactual, and the reason the option exists: the identical setup, verification
        # off, deletes a file that is not a duplicate of the one being kept.
        app = scanned({"a.bin": MD5_COLLISION_A, "b.bin": MD5_COLLISION_B})
        first, second = (_File(tmp_path / name) for name in ("a.bin", "b.bin"))
        group = Group()
        group.add_match(Match(first, second, 100, kind=MatchKind.EXACT))
        app.results.groups = [group]
        app.options["verify_before_delete"] = False

        dupe = group.dupes[0]
        app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert not dupe.path.exists()

    def test_genuinely_identical_files_are_still_deleted(self, scanned):
        # The control. An option that refused everything would be safe and useless.
        app = scanned({"a.bin": b"identical" * 100, "b.bin": b"identical" * 100})
        app.options["verify_before_delete"] = True
        dupe = app.results.groups[0].dupes[0]
        app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert not dupe.path.exists()

    def test_it_does_nothing_unless_asked(self, scanned, monkeypatch):
        # Off by default, because it doubles the reading a deletion does.
        app = scanned({"a.bin": b"identical" * 100, "b.bin": b"identical" * 100})
        assert app.options["verify_before_delete"] is False
        called = []
        monkeypatch.setattr("core.deletion_plan.verify_identical", lambda *a: called.append(1) or True)
        dupe = app.results.groups[0].dupes[0]
        app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert called == [], "the files were compared without being asked to be"

    def test_a_changed_file_is_still_refused_first(self, scanned):
        # Verification is an addition to check_deletable, not a replacement: a file that changed
        # since the scan must still be refused for that reason.
        app = scanned({"a.bin": b"identical" * 100, "b.bin": b"identical" * 100})
        app.options["verify_before_delete"] = True
        dupe = app.results.groups[0].dupes[0]
        dupe.path.write_bytes(b"changed since the scan")
        with pytest.raises(OSError):
            app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert dupe.path.exists()


class TestTheStatusExists:
    def test_differs_is_a_distinct_refusal(self):
        # Not folded into CHANGED: "differs from the file being kept" and "changed since the
        # scan" are different facts, and a user acting on the first needs to know which.
        assert DeleteStatus.DIFFERS not in {
            DeleteStatus.OK,
            DeleteStatus.GONE,
            DeleteStatus.SYMLINK,
            DeleteStatus.UNREADABLE,
            DeleteStatus.CHANGED,
        }
