# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Collapsing duplicate groups into the folder pair that explains them (issue #122).

The feature's value is a smaller number of decisions, so most of these are about *not*
collapsing: a rollup that groups folders which merely happen to share a few files invites the
user to act on a pattern that is not there, one decision at a time being safer than one wrong
decision covering four hundred files.

The other half is the promise the row makes. "437 files" has to be 437 files that would
actually be marked, which is checked against a real `Results` rather than assumed.
"""

from pathlib import Path

import pytest

from core.engine import Group, Match
from core.folder_rollup import MIN_FILES, MIN_SHARE, Rollup, build_rollup, candidate_pairs
from core.results import Results
from core.tests.base import NamedObject


class FakeApp:
    def __init__(self):
        self.options = {}


def native(path):
    """A folder string as this platform writes it.

    The rollup reports whatever ``pathlib`` produces, so "/backup" is ``\\backup`` on Windows.
    Comparing against a hardcoded POSIX string passes on macOS and Linux and fails there.
    """
    return str(Path(path))


def file_at(path, size=1000):
    """A file object whose .path is the one given. NamedObject derives it from folder+name."""
    path = Path(path)
    return NamedObject(name=path.name, size=size, folder=str(path.parent))


def results_with(pairs):
    """Real Results built from (ref_path, dupe_path) pairs, one group each."""
    groups = []
    for ref_path, dupe_path in pairs:
        ref, dupe = file_at(ref_path), file_at(dupe_path)
        group = Group()
        group.add_match(Match(ref, dupe, 100))
        groups.append(group)
    results = Results(FakeApp())
    results.groups = groups
    return results


def shadowed(count, dupe_dir="/backup/2023", ref_dir="/photos/2023", start=0):
    return [(f"{ref_dir}/img{i}.jpg", f"{dupe_dir}/img{i}.jpg") for i in range(start, start + count)]


class TestCandidatePairs:
    def test_a_pair_is_offered_at_every_combination_of_depths(self):
        # Every ancestor against every ancestor, not a lockstep walk: the explaining pair is
        # often at different depths on the two sides.
        pairs = list(candidate_pairs(Path("/backup/2023/a.jpg"), Path("/photos/2023/a.jpg")))
        assert set(pairs) == {
            (native("/backup/2023"), native("/photos/2023")),
            (native("/backup/2023"), native("/photos")),
            (native("/backup"), native("/photos/2023")),
            (native("/backup"), native("/photos")),
        }

    def test_folders_at_different_depths_still_pair(self):
        # The case a lockstep walk cannot reach. Without this, a flat /Downloads duplicating a
        # nested /photos/set0 produces no usable pair at all -- only ("/", "/photos"), which
        # claims the filesystem root duplicates a folder.
        pairs = list(candidate_pairs(Path("/Downloads/a.jpg"), Path("/photos/set0/a.jpg")))
        assert (native("/Downloads"), native("/photos")) in pairs
        assert not any(dupe_folder == native("/") or ref_folder == native("/") for dupe_folder, ref_folder in pairs)

    def test_a_folder_never_pairs_with_its_own_ancestor(self):
        # "/photos duplicates /photos/2023" is not a statement about anything.
        pairs = list(candidate_pairs(Path("/photos/2023/a.jpg"), Path("/photos/2024/a.jpg")))
        assert pairs == [(native("/photos/2023"), native("/photos/2024"))]

    def test_a_shared_ancestor_ends_the_walk(self):
        # /media is common to both, and a folder does not duplicate itself. Reporting it would
        # claim the whole drive duplicates the whole drive.
        pairs = list(candidate_pairs(Path("/media/backup/a.jpg"), Path("/media/photos/a.jpg")))
        assert pairs == [(native("/media/backup"), native("/media/photos"))]

    def test_two_files_in_the_same_folder_explain_nothing(self):
        assert list(candidate_pairs(Path("/photos/a.jpg"), Path("/photos/a copy.jpg"))) == []


class TestCollapsing:
    def test_a_backup_shadowing_an_original_becomes_one_row(self):
        results = results_with(shadowed(40))
        rollup = build_rollup(results)
        assert len(rollup.pairs) == 1
        assert rollup.pairs[0].file_count == 40
        assert rollup.decisions_saved == 39, "forty decisions become one"

    def test_the_outermost_pair_is_reported_not_every_depth(self):
        # /backup duplicating /photos implies /backup/2023 duplicating /photos/2023. Reporting
        # both tells the user the same thing twice and doubles the list they came here to
        # shorten.
        results = results_with(
            shadowed(20, "/backup/2023", "/photos/2023") + shadowed(20, "/backup/2024", "/photos/2024")
        )
        rollup = build_rollup(results)
        assert [(p.dupe_folder, p.ref_folder) for p in rollup.pairs] == [(native("/backup"), native("/photos"))]
        assert rollup.pairs[0].file_count == 40

    def test_the_rows_lead_with_the_most_space(self):
        results = results_with(shadowed(10, "/small", "/photos", start=0) + shadowed(10, "/big", "/photos", start=100))
        for group in results.groups:
            for dupe in group.dupes:
                if str(dupe.path).startswith(native("/big")):
                    dupe.size = 100_000
        rollup = build_rollup(results)
        assert rollup.pairs[0].dupe_folder == native("/big")

    def test_bytes_count_only_what_would_be_deleted(self):
        # The reference stays. Counting it would overstate the benefit, which is the number
        # that erodes trust when it turns out wrong.
        results = results_with(shadowed(10))
        rollup = build_rollup(results)
        assert rollup.pairs[0].total_bytes == 10 * 1000


class TestNotCollapsing:
    def test_a_handful_of_shared_files_is_not_a_pattern(self):
        # Two folders overlapping by a few files is a coincidence. Presenting it as one
        # decision invites acting on it as though it were a backup relationship.
        results = results_with(shadowed(MIN_FILES - 1, "/Downloads", "/photos"))
        assert build_rollup(results).pairs == []

    def test_a_folder_whose_files_scatter_is_not_explained(self):
        # A download folder whose contents duplicate six unrelated places is not a shadow of
        # any one of them, however many files it holds. The destinations have to be genuinely
        # unrelated: scattering across /photos/set0../set5 is still wholly explained by
        # /photos, and reporting that is correct rather than noise.
        roots = ["/photos", "/archive", "/media", "/scans", "/exports", "/old"]
        pairs = [(f"{roots[i % 6]}/img{i}.jpg", f"/Downloads/img{i}.jpg") for i in range(30)]
        rollup = build_rollup(results_with(pairs))
        assert rollup.pairs == []
        assert len(rollup.unexplained) == 30

    def test_a_folder_scattered_within_one_tree_is_still_explained_by_that_tree(self):
        # The counterpart to the above, and the reason the walk goes up rather than stopping at
        # the immediate parent: "everything in Downloads is already somewhere under /photos" is
        # exactly the useful statement.
        pairs = [(f"/photos/set{i % 6}/img{i}.jpg", f"/Downloads/img{i}.jpg") for i in range(30)]
        rollup = build_rollup(results_with(pairs))
        assert [(p.dupe_folder, p.ref_folder) for p in rollup.pairs] == [(native("/Downloads"), native("/photos"))]

    def test_unrelated_folders_never_pair(self):
        results = results_with([(f"/a{i}/f.jpg", f"/b{i}/f.jpg") for i in range(20)])
        assert build_rollup(results).pairs == []

    def test_empty_results_produce_an_empty_rollup(self):
        rollup = build_rollup(results_with([]))
        assert rollup == Rollup(pairs=[], unexplained=[])
        assert rollup.decisions_saved == 0


class TestTheCountIsThePromise:
    """The row says "437 files"; marking it must mark exactly those."""

    def test_every_counted_dupe_can_actually_be_marked(self):
        results = results_with(shadowed(20))
        rollup = build_rollup(results)
        pair = rollup.pairs[0]

        results.mark_multiple(pair.dupes)

        # mark_multiple reports nothing, so the count it produced is the check that matters:
        # every file the row promised was accepted for marking, and no others.
        assert results.mark_count == pair.file_count

    def test_references_are_never_counted(self):
        # A reference cannot be marked, so counting one would put a file in the total that no
        # deletion will ever touch.
        results = results_with(shadowed(20))
        rollup = build_rollup(results)
        refs = {id(group.ref) for group in results.groups}
        assert not any(id(dupe) in refs for dupe in rollup.pairs[0].dupes)

    def test_a_file_in_a_reference_folder_is_never_counted(self):
        # The case that matters, and the one a naive fixture misses. A file from a reference
        # folder appears in group.dupes but can never be marked -- that is the whole point of
        # a reference folder. Counting it would put a protected file in the row's total and
        # promise a deletion that will never happen.
        results = results_with(shadowed(20))
        protected = []
        for group in results.groups[:8]:
            for dupe in group.dupes:
                dupe.is_ref = True
                protected.append(dupe)
        assert all(not results.is_markable(dupe) for dupe in protected), "the fixture must be unmarkable"

        rollup = build_rollup(results)

        counted = [dupe for pair in rollup.pairs for dupe in pair.dupes] + list(rollup.unexplained)
        assert not any(dupe in protected for dupe in counted), "a protected file was counted"
        assert all(results.is_markable(dupe) for dupe in counted)

    def test_a_filtered_view_only_counts_what_it_shows(self):
        # apply_filter narrows results.groups itself, so the rollup sees the filtered view --
        # but the count still has to match what marking would do within it.
        results = results_with(shadowed(20))
        results.apply_filter("img1")
        rollup = build_rollup(results)

        counted = [dupe for pair in rollup.pairs for dupe in pair.dupes] + list(rollup.unexplained)
        results.mark_multiple(counted)
        assert results.mark_count == len(counted)

    def test_no_dupe_appears_in_two_pairs(self):
        # Overlapping rows would double-count the space and mark some files twice.
        results = results_with(
            shadowed(20, "/backup/2023", "/photos/2023") + shadowed(20, "/backup/2024", "/photos/2024")
        )
        rollup = build_rollup(results)
        seen = [id(dupe) for pair in rollup.pairs for dupe in pair.dupes]
        assert len(seen) == len(set(seen))

    def test_everything_markable_is_either_explained_or_listed(self):
        # Nothing may be silently dropped: a file missing from both would be invisible in the
        # rolled-up view and never reviewed.
        results = results_with(shadowed(20) + shadowed(2, "/odd", "/photos", start=500))
        rollup = build_rollup(results)
        accounted = {id(d) for pair in rollup.pairs for d in pair.dupes} | {id(d) for d in rollup.unexplained}
        markable = {id(d) for g in results.groups for d in g.dupes if results.is_markable(d)}
        assert accounted == markable


class TestDirection:
    def test_a_pair_is_not_generalised_past_what_it_gains(self):
        # /Users/k/Downloads duplicating /Volumes/Photos/misc is equally "explained" by
        # /Users -> /Volumes, which covers the same files and says nothing anyone can act on.
        # Rolling up is only worth it where it merges genuinely different subsets.
        pairs = [(f"/Volumes/Photos/misc/p{i}.jpg", f"/Users/k/Downloads/p{i}.jpg") for i in range(20)]
        rollup = build_rollup(results_with(pairs))
        assert [(p.dupe_folder, p.ref_folder) for p in rollup.pairs] == [
            (native("/Users/k/Downloads"), native("/Volumes/Photos/misc"))
        ]

    def test_a_pair_claims_no_direction_by_default(self):
        # dupeGuru picks the reference by size unless told otherwise, so which side is the
        # "original" is usually incidental. Drawing an arrow would invent an answer.
        results = results_with(shadowed(10))
        assert build_rollup(results).pairs[0].direction_is_explicit is False

    def test_a_reference_folder_makes_the_direction_explicit(self):
        # The predicate is asked about the folder actually reported. With every file under
        # /photos/2023 there is nothing to gain by generalising to /photos, so the specific
        # pair is the one shown.
        results = results_with(shadowed(10))
        rollup = build_rollup(results, is_reference_folder=lambda path: path == native("/photos/2023"))
        assert rollup.pairs[0].ref_folder == native("/photos/2023")
        assert rollup.pairs[0].direction_is_explicit is True

    def test_only_the_folder_the_user_marked_counts(self):
        results = results_with(shadowed(10))
        rollup = build_rollup(results, is_reference_folder=lambda path: path == native("/somewhere/else"))
        assert rollup.pairs[0].direction_is_explicit is False


class TestThresholds:
    def test_the_share_threshold_is_between_a_half_and_one(self):
        # Below a half, "most of this folder" is false. At one, a single stray file in an
        # otherwise perfect backup suppresses the row entirely.
        assert 0.5 < MIN_SHARE < 1.0

    def test_the_threshold_is_documented_as_provisional(self):
        # The issue asks for this to come from a real corpus rather than a guess. It has not
        # been derived yet, and the constant says so; this fails if the note is dropped without
        # the work being done.
        import core.folder_rollup as rollup_module

        assert "Provisional" in rollup_module.__doc__ or "Provisional" in _module_source(rollup_module)


def _module_source(module):
    return Path(module.__file__).read_text()


@pytest.mark.parametrize("count", [MIN_FILES - 1, MIN_FILES])
def test_the_file_threshold_is_the_boundary_it_claims(count):
    results = results_with(shadowed(count, "/backup", "/photos"))
    rollup = build_rollup(results)
    assert bool(rollup.pairs) is (count >= MIN_FILES)
