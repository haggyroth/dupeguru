# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Finding visually similar images during a standard scan (issue #128).

Two things carry the risk here, and neither is the matching itself.

A standard scan collects ``se.fs.File``, which cannot be compared perceptually, so the images
are re-read as photo objects purely to be matched. Every result has to be reported against the
file the scan actually collected -- report the stand-in and the results hold two objects per
path, and grouping, marking and deletion all act on the wrong one.

And a resemblance is not an identity. Two files whose block signatures agree exactly can be a
re-encode, a crop or a resize, so "100%" from the picture matcher and "100%" from the content
matcher are very different claims. The kind travels with the match so nothing downstream has to
guess which it was looking at.
"""

from pathlib import Path

import pytest

from core import combined_scan
from core.engine import Match, MatchKind, get_groups
from core.tests.base import NamedObject


class FakePhoto:
    """Stands in for a platform photo class, without needing an image decoder."""

    HANDLED = {".jpg", ".png"}

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.name
        self.is_ref = False

    @classmethod
    def can_handle(cls, path):
        return Path(path).suffix.lower() in cls.HANDLED


def file_at(path):
    path = Path(path)
    return NamedObject(name=path.name, size=100, folder=str(path.parent))


class TestPickingOutImages:
    def test_only_images_get_a_stand_in(self):
        files = [file_at("/a/photo.png"), file_at("/a/notes.txt"), file_at("/a/scan.jpg")]
        photos = combined_scan.photos_in(files, FakePhoto)
        assert sorted(f.name for f in photos.values()) == ["photo.png", "scan.jpg"]

    def test_each_stand_in_remembers_the_file_it_stands_for(self):
        # The mapping is the whole mechanism: results must name the collected file.
        original = file_at("/a/photo.png")
        photos = combined_scan.photos_in([original], FakePhoto)
        assert list(photos.values()) == [original]

    def test_reference_status_travels_with_the_stand_in(self):
        # The picture matcher refuses to pair two reference files. A stand-in that did not know
        # it stood for one would hand back a match between two protected files.
        protected = file_at("/a/photo.png")
        protected.is_ref = True
        ordinary = file_at("/a/other.png")
        photos = combined_scan.photos_in([protected, ordinary], FakePhoto)
        by_name = {file.name: photo for photo, file in photos.items()}
        assert by_name["photo.png"].is_ref is True
        assert by_name["other.png"].is_ref is False

    def test_an_unreadable_image_is_skipped_rather_than_fatal(self):
        class Exploding(FakePhoto):
            def __init__(self, path):
                raise OSError("cannot decode")

        files = [file_at("/a/photo.png")]
        assert combined_scan.photos_in(files, Exploding) == {}


class TestDegradingQuietly:
    def test_no_image_decoder_means_no_picture_matches_not_a_failure(self, monkeypatch):
        # The command line has no Qt binding unless one happens to be installed. The content
        # matches are the greater part of what was asked for.
        monkeypatch.setattr(combined_scan, "photo_class", lambda: None)
        assert combined_scan.picture_matches_over([file_at("/a/photo.png")], j=None) == []

    def test_a_scan_with_one_image_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(combined_scan, "photo_class", lambda: FakePhoto)
        assert combined_scan.picture_matches_over([file_at("/a/photo.png")], j=None) == []

    def test_a_scan_with_no_images_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(combined_scan, "photo_class", lambda: FakePhoto)
        files = [file_at("/a/notes.txt"), file_at("/a/report.pdf")]
        assert combined_scan.picture_matches_over(files, j=None) == []


class TestMerging:
    def _pair(self, kind, percentage=100):
        a, b = NamedObject("a"), NamedObject("b")
        return a, b, Match(a, b, percentage, kind=kind)

    def test_both_matchers_contribute(self):
        a, b = NamedObject("a"), NamedObject("b")
        c, d = NamedObject("c"), NamedObject("d")
        content = [Match(a, b, 100, kind=MatchKind.EXACT)]
        pictures = [Match(c, d, 95, kind=MatchKind.RESEMBLANCE)]
        assert len(combined_scan.merge_matches(content, pictures)) == 2

    def test_a_pair_found_by_both_is_reported_once_as_the_stronger_claim(self):
        # Two byte-identical images are found twice: their bytes agree and so does their
        # appearance. Reporting both would put the same pair in the results at two different
        # confidences.
        a, b = NamedObject("a"), NamedObject("b")
        merged = combined_scan.merge_matches(
            [Match(a, b, 100, kind=MatchKind.EXACT)],
            [Match(a, b, 100, kind=MatchKind.RESEMBLANCE)],
        )
        assert len(merged) == 1
        assert merged[0].kind == MatchKind.EXACT

    def test_the_pair_is_recognised_whichever_way_round_it_is(self):
        a, b = NamedObject("a"), NamedObject("b")
        merged = combined_scan.merge_matches(
            [Match(a, b, 100, kind=MatchKind.EXACT)],
            [Match(b, a, 100, kind=MatchKind.RESEMBLANCE)],
        )
        assert len(merged) == 1

    def test_neither_list_being_empty_is_required(self):
        a, b = NamedObject("a"), NamedObject("b")
        assert combined_scan.merge_matches([], [Match(a, b, 95, kind=MatchKind.RESEMBLANCE)])
        assert combined_scan.merge_matches([Match(a, b, 100, kind=MatchKind.EXACT)], [])


class TestProvenance:
    def test_a_content_match_says_it_compared_the_content(self):
        from core.engine import getmatches_by_contents

        a, b = NamedObject("a", size=10), NamedObject("b", size=10)
        for f in (a, b):
            f.digest = f.digest_partial = f.digest_samples = "same"
        assert getmatches_by_contents([a, b])[0].kind == MatchKind.EXACT

    def test_an_unstated_kind_makes_the_weakest_claim(self):
        # A match wrongly labelled EXACT invites deleting a file nobody compared; one wrongly
        # labelled METADATA only invites a second look. Understating is the safe default.
        a, b = NamedObject("a"), NamedObject("b")
        assert Match(a, b, 100).kind == MatchKind.METADATA

    def test_only_an_exact_match_is_treated_as_certain(self):
        assert MatchKind.EXACT in MatchKind.CERTAIN
        assert MatchKind.RESEMBLANCE not in MatchKind.CERTAIN
        assert MatchKind.METADATA not in MatchKind.CERTAIN


class TestMixingTransitiveAndNot:
    """The issue's main worry, and it turns out to be handled already.

    Exact matching is transitive and perceptual matching is not: A looking like B and B looking
    like C says nothing about A and C. Mixing the two in one grouping pass sounds like it would
    strain the greedy merge -- but a group only admits a file that matches *every* other member,
    so a non-transitive edge cannot drag in a file that does not belong.
    """

    def test_a_file_matching_only_one_member_does_not_join_the_group(self):
        a, b, c = (NamedObject(n) for n in "abc")
        groups = get_groups(
            [
                Match(a, b, 100, kind=MatchKind.EXACT),
                Match(b, c, 95, kind=MatchKind.RESEMBLANCE),
            ]
        )
        assert len(groups) == 1
        assert sorted(f.name for f in groups[0]) == ["a", "b"], "c matched only b, so it stays out"

    def test_a_file_matching_every_member_does_join(self):
        a, b, c = (NamedObject(n) for n in "abc")
        groups = get_groups(
            [
                Match(a, b, 100, kind=MatchKind.EXACT),
                Match(b, c, 95, kind=MatchKind.RESEMBLANCE),
                Match(a, c, 95, kind=MatchKind.RESEMBLANCE),
            ]
        )
        assert len(groups) == 1
        assert sorted(f.name for f in groups[0]) == ["a", "b", "c"]

    def test_a_group_can_hold_both_kinds_and_says_so(self):
        # An original, its byte-identical copy, and a resize of it. The group is genuine and
        # mixed, and the kinds are what tell the user which member is which.
        a, b, c = (NamedObject(n) for n in "abc")
        groups = get_groups(
            [
                Match(a, b, 100, kind=MatchKind.EXACT),
                Match(a, c, 99, kind=MatchKind.RESEMBLANCE),
                Match(b, c, 99, kind=MatchKind.RESEMBLANCE),
            ]
        )
        kinds = {m.kind for m in groups[0].matches}
        assert kinds == {MatchKind.EXACT, MatchKind.RESEMBLANCE}


class TestScannerIntegration:
    def test_combining_is_off_unless_asked_for(self):
        from core.scanner import Scanner

        assert Scanner.combine_picture_matching is False

    def test_the_picture_knobs_exist_on_the_base_scanner(self):
        # start_scanning copies an option onto the scanner only when the attribute is already
        # there, so one declared solely on ScannerPE is silently dropped in standard mode.
        from core.scanner import Scanner

        for name in ("match_scaled", "match_rotated", "cache_path"):
            assert hasattr(Scanner, name), f"{name} is not on the base scanner"

    def test_combining_only_applies_to_a_contents_scan(self, monkeypatch):
        # Merging resemblances into a filename or tag scan would combine two weak signals and
        # call the result a duplicate.
        from core import se
        from core.scanner import ScanType

        called = []
        monkeypatch.setattr(combined_scan, "picture_matches_over", lambda *a, **k: called.append(1) or [])

        scanner = se.scanner.ScannerSE()
        scanner.combine_picture_matching = True
        scanner.scan_type = ScanType.FILENAME
        scanner.get_dupe_groups([])
        assert called == [], "a filename scan must not pull in picture matches"


@pytest.mark.skipif(combined_scan.photo_class() is None, reason="no image decoder available in this environment")
def test_the_stand_ins_never_reach_the_results():
    """Whatever the matcher saw, the results name the files the scan collected."""
    files = [file_at("/a/photo.png"), file_at("/a/photo2.png")]
    photos = combined_scan.photos_in(files, FakePhoto)
    assert all(original in files for original in photos.values())
