# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Triaging groups by what is known about them (issue #124).

The whole feature exists so a bulk action can be aimed at the groups that deserve one, which
means every test here is really asking the same question: can a group reach the top tier
without having earned it? A group wrongly placed there is the exact failure this was built to
prevent, so the interesting cases are all the ways something might be over-credited -- a sampled
hash read as a full comparison, a resemblance vouched for by a filename, one strong pair
carrying a weak one that shares its group.
"""

from core.confidence import (
    Confidence,
    classify_group,
    classify_match,
    corroboration_of,
    tally,
)
from core.engine import Group, Match, MatchKind
from core.tests.base import NamedObject


def group_of(*matches):
    group = Group()
    for match in matches:
        group.add_match(match)
    return group


def files(*names, folder="basepath"):
    return [NamedObject(name, folder=folder) for name in names]


def exact(first, second, partial=False):
    return Match(first, second, 100, partial=partial, kind=MatchKind.EXACT)


def resemblance(first, second, percentage=95):
    return Match(first, second, percentage, kind=MatchKind.RESEMBLANCE)


class TestWhatOnePairEstablishes:
    def test_a_full_content_comparison_establishes_content(self):
        a, b = files("a.txt", "b.txt")
        assert classify_match(exact(a, b)) == Confidence.CONTENT

    def test_a_sampled_hash_establishes_nothing(self):
        # kind=EXACT and partial=True together mean "believed identical, only sampled". Reading
        # the kind alone would promote every big-file match into the bulk-action pile.
        a, b = files("a.txt", "b.txt")
        assert classify_match(exact(a, b, partial=True)) == Confidence.UNCONFIRMED

    def test_a_resemblance_establishes_nothing(self):
        a, b = files("a.png", "b.png")
        assert classify_match(resemblance(a, b, percentage=100)) == Confidence.UNCONFIRMED

    def test_a_metadata_match_establishes_nothing(self):
        a, b = files("a.mp3", "b.mp3")
        assert classify_match(Match(a, b, 100, kind=MatchKind.METADATA)) == Confidence.UNCONFIRMED

    def test_a_match_with_no_kind_at_all_establishes_nothing(self):
        # Saved results from an older version have no kind recorded.
        class Old:
            percentage = 100
            partial = False

        assert classify_match(Old()) == Confidence.UNCONFIRMED


class TestCorroboration:
    def test_a_reference_folder_member_corroborates(self):
        # The user's own statement about where the originals live.
        a, b = files("one.txt", "two.txt")
        a.is_ref = True
        assert corroboration_of(group_of(exact(a, b)))

    def test_one_shared_filename_corroborates(self):
        a = NamedObject("report.pdf", folder="/projects/alpha")
        b = NamedObject("report.pdf", folder="/backup")
        assert corroboration_of(group_of(exact(a, b)))

    def test_differing_filenames_corroborate_nothing(self):
        a = NamedObject("report.pdf", folder="/projects")
        b = NamedObject("report-final.pdf", folder="/backup")
        assert corroboration_of(group_of(exact(a, b))) == ""

    def test_a_near_name_is_not_a_shared_name(self):
        # "report (1).pdf" beside "report.pdf" is a guess about intent, not evidence.
        a = NamedObject("report.pdf", folder="/d")
        b = NamedObject("report (1).pdf", folder="/d")
        assert corroboration_of(group_of(exact(a, b))) == ""

    def test_every_member_must_share_the_name_not_just_one_pair(self):
        # The tier governs marking the whole group, and the odd-named member gets marked too.
        a = NamedObject("report.pdf", folder="/a")
        b = NamedObject("report.pdf", folder="/b")
        c = NamedObject("something-else.pdf", folder="/c")
        group = group_of(exact(a, b), exact(b, c), exact(a, c))
        assert len(group) == 3, "fixture must build a real 3-member group"
        assert corroboration_of(group) == ""

    def test_the_reason_is_stated_rather_than_implied(self):
        a, b = files("one.txt", "two.txt")
        a.is_ref = True
        assert "Reference" in corroboration_of(group_of(exact(a, b)))


class TestClassifyingAGroup:
    def test_full_content_with_a_shared_name_is_corroborated(self):
        a = NamedObject("photo.jpg", folder="/pictures")
        b = NamedObject("photo.jpg", folder="/backup")
        assert classify_group(group_of(exact(a, b))).tier == Confidence.CORROBORATED

    def test_full_content_alone_stops_at_content(self):
        a = NamedObject("photo.jpg", folder="/pictures")
        b = NamedObject("holiday.jpg", folder="/backup")
        assert classify_group(group_of(exact(a, b))).tier == Confidence.CONTENT

    def test_a_sampled_hash_stays_unconfirmed_however_well_corroborated(self):
        # The corroboration is real; the content comparison is what is missing, and a filename
        # cannot stand in for bytes nobody read.
        a = NamedObject("movie.mkv", folder="/media")
        b = NamedObject("movie.mkv", folder="/backup")
        a.is_ref = True
        assert classify_group(group_of(exact(a, b, partial=True))).tier == Confidence.UNCONFIRMED

    def test_a_resemblance_stays_unconfirmed_even_with_a_shared_name(self):
        # A matching name is exactly what a re-encode or a copy-and-edit leaves behind, so it is
        # the least trustworthy corroboration available for a resemblance.
        a = NamedObject("photo.jpg", folder="/pictures")
        b = NamedObject("photo.jpg", folder="/exports")
        assert classify_group(group_of(resemblance(a, b, percentage=100))).tier == Confidence.UNCONFIRMED

    def test_a_group_is_only_as_understood_as_its_weakest_pair(self):
        # From a combined scan (#128): an original, its byte-identical copy, and a resize.
        # Taking the strongest pair would let the exact match vouch for the resemblance.
        a = NamedObject("photo.jpg", folder="/pictures")
        b = NamedObject("photo.jpg", folder="/backup")
        c = NamedObject("photo.jpg", folder="/exports")
        group = group_of(exact(a, b), resemblance(a, c), resemblance(b, c))
        assert len(group) == 3, "fixture must build a real 3-member group"
        assert classify_group(group).tier == Confidence.UNCONFIRMED

    def test_a_group_linked_by_nothing_is_unconfirmed(self):
        assert classify_group(Group()).tier == Confidence.UNCONFIRMED

    def test_every_group_carries_a_reason(self):
        a = NamedObject("photo.jpg", folder="/pictures")
        b = NamedObject("holiday.jpg", folder="/backup")
        for group in (group_of(exact(a, b)), group_of(resemblance(a, b)), Group()):
            assert classify_group(group).reason


class TestTheTiersThemselves:
    def test_the_order_runs_weakest_to_strongest(self):
        assert Confidence.ORDER == (
            Confidence.UNCONFIRMED,
            Confidence.CONTENT,
            Confidence.CORROBORATED,
        )

    def test_the_weakest_of_several_wins(self):
        assert Confidence.weakest([Confidence.CORROBORATED, Confidence.UNCONFIRMED]) == Confidence.UNCONFIRMED
        assert Confidence.weakest([Confidence.CORROBORATED, Confidence.CONTENT]) == Confidence.CONTENT

    def test_the_weakest_of_nothing_is_unconfirmed(self):
        assert Confidence.weakest([]) == Confidence.UNCONFIRMED

    def test_no_tier_name_promises_safety(self):
        # "Safe" is a promise, and bulk-marking on a broken promise is the failure this feature
        # exists to prevent. The labels describe evidence and leave the judgement to the user.
        words = {"safe", "safely", "certain", "guaranteed", "ok"}
        for label in Confidence.LABELS.values():
            assert not (words & set(label.lower().split())), f"{label!r} reads as a safety promise"

    def test_every_tier_can_be_labelled_and_explained(self):
        for tier in Confidence.ORDER:
            assert Confidence.LABELS[tier]
            assert Confidence.EXPLANATIONS[tier]


class TestTally:
    def test_groups_are_counted_by_tier(self):
        corroborated = group_of(exact(NamedObject("same.txt", folder="/a"), NamedObject("same.txt", folder="/b")))
        content = group_of(exact(NamedObject("a.txt"), NamedObject("b.txt")))
        unconfirmed = group_of(resemblance(NamedObject("c.jpg"), NamedObject("d.jpg")))
        counts = tally([corroborated, content, unconfirmed, unconfirmed])
        assert counts == {
            Confidence.CORROBORATED: 1,
            Confidence.CONTENT: 1,
            Confidence.UNCONFIRMED: 2,
        }

    def test_an_empty_tier_is_reported_as_zero_rather_than_omitted(self):
        counts = tally([group_of(exact(NamedObject("a.txt"), NamedObject("b.txt")))])
        assert counts[Confidence.UNCONFIRMED] == 0
        assert set(counts) == set(Confidence.ORDER)

    def test_no_groups_is_all_zeroes(self):
        assert tally([]) == {tier: 0 for tier in Confidence.ORDER}
