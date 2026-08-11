# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A group built from an equivalence class behaves exactly like one built from pairs (#180).

k byte-identical files produce k(k-1)/2 matches that differ only in their endpoints, and
``Group`` keeps every one of them for the life of the results -- ``discard_matches`` drops only
matches whose files left the group, so a clique discards nothing. That is 284 million namedtuples
for a cluster of 23,857 files, about 23 GiB, to record one fact.

Deriving the matches from the members instead is an exact representation change. The danger is
that it is *nearly* exact: a shortcut that skips ``get_match_of`` would make
``core/deletion_plan.py`` read a sampled-hash duplicate as fully compared, because it treats a
missing match as ``partial=False`` -- a data-safety regression wearing the costume of a memory
optimisation.

So the test that matters is differential, and it compares every observable rather than a chosen
few. If a uniform group and a pair-built group can be told apart by anything the application
reads, this is not safe.
"""

import random

import pytest

from core.engine import Group, Match, MatchKind, UniformMatches, get_groups
from core.tests.base import NamedObject


def files(count, prefix="f"):
    return [NamedObject(f"{prefix}{i}", size=100) for i in range(count)]


def pair_built(members, percentage=100, partial=False, kind=MatchKind.EXACT):
    """The old way: feed every pair to add_match."""
    group = Group()
    for i, first in enumerate(members):
        for second in members[i + 1 :]:
            group.add_match(Match(first, second, percentage, partial=partial, kind=kind))
    return group


def observables(group):
    """Everything the application can read off a group."""
    return {
        "members": [f.name for f in group],
        "ordered": [f.name for f in group.ordered],
        "unordered": sorted(f.name for f in group.unordered),
        "ref": None if group.ref is None else group.ref.name,
        "dupes": [f.name for f in group.dupes],
        "len": len(group),
        "percentage": group.percentage,
        "match_count": len(list(group.matches)),
        "matches": sorted(
            (tuple(sorted((m.first.name, m.second.name))), m.percentage, m.partial, m.kind) for m in group.matches
        ),
        "match_of": {
            f.name: (
                None
                if group.ref is None or group.get_match_of(f) is None
                else (group.get_match_of(f).percentage, group.get_match_of(f).partial, group.get_match_of(f).kind)
            )
            for f in group
        },
        "any_partial": any(getattr(m, "partial", False) for m in group.matches),
        "truthy_matches": bool(group.matches),
    }


class TestTheTwoAgree:
    @pytest.mark.parametrize("k", [2, 3, 5, 12])
    @pytest.mark.parametrize("partial", [False, True])
    def test_every_observable_is_identical(self, k, partial):
        members = files(k)
        assert observables(pair_built(members, partial=partial)) == observables(
            Group.from_identical(members, partial=partial)
        )

    @pytest.mark.parametrize("k", [2, 4, 9])
    def test_they_stay_identical_after_prioritizing(self, k):
        # The case the obvious shortcut gets wrong. Reordering changes which matches contain the
        # reference, and a set built around the original reference stops answering for the new
        # one -- which is where get_match_of starts returning None.
        rng = random.Random(k)
        order = {f"f{i}": rng.random() for i in range(k)}
        members = files(k)
        a, b = pair_built(members), Group.from_identical(members)
        for group in (a, b):
            group.prioritize(lambda f: order[f.name])
        assert observables(a) == observables(b)

    @pytest.mark.parametrize("k", [3, 6])
    def test_they_stay_identical_after_switching_the_reference(self, k):
        members = files(k)
        a, b = pair_built(members), Group.from_identical(members)
        for group in (a, b):
            group.switch_ref(group.ordered[-1])
        assert observables(a) == observables(b)

    @pytest.mark.parametrize("k", [3, 7])
    def test_they_stay_identical_after_removing_a_member(self, k):
        members = files(k)
        a, b = pair_built(members), Group.from_identical(members)
        for group in (a, b):
            group.remove_dupe(group.ordered[1])
        assert observables(a) == observables(b)

    def test_they_agree_when_removal_empties_the_group(self):
        members = files(2)
        a, b = pair_built(members), Group.from_identical(members)
        for group in (a, b):
            group.remove_dupe(group.ordered[1])
        assert observables(a) == observables(b)

    def test_discarding_matches_agrees(self):
        # A complete clique has no orphans, so both must return nothing and keep every match.
        members = files(5)
        a, b = pair_built(members), Group.from_identical(members)
        assert a.discard_matches() == b.discard_matches() == set()
        assert observables(a) == observables(b)


class TestTheDangerousOne:
    """The partial flag must survive, on every member, in every order."""

    @pytest.mark.parametrize("k", [2, 5, 11])
    def test_a_sampled_group_reports_partial_for_every_member(self, k):
        group = Group.from_identical(files(k), partial=True)
        for dupe in group.dupes:
            match = group.get_match_of(dupe)
            assert match is not None, "a missing match is read as partial=False by the deletion plan"
            assert match.partial is True

    def test_it_still_reports_partial_after_the_reference_moves(self):
        group = Group.from_identical(files(6), partial=True)
        group.switch_ref(group.ordered[-1])
        for dupe in group.dupes:
            match = group.get_match_of(dupe)
            assert match is not None and match.partial is True

    def test_the_deletion_plan_sees_the_partial_flag(self):
        # Reproduces exactly what core/deletion_plan.py does, since that is the consumer whose
        # fallback turns a missing match into a false claim of full comparison.
        group = Group.from_identical(files(4), partial=True)
        group.prioritize(lambda f: f.name)
        for dupe in group.dupes:
            match = group.get_match_of(dupe)
            is_partial = bool(getattr(match, "partial", False)) if match else False
            assert is_partial is True, "a sampled duplicate would be reported as fully compared"

    def test_the_reference_itself_has_no_match_of_its_own(self):
        # True of both representations; asserted so the synthesised path cannot invent one.
        group = Group.from_identical(files(3))
        assert group.get_match_of(group.ref) is None

    def test_a_file_outside_the_group_has_no_match(self):
        group = Group.from_identical(files(3))
        assert group.get_match_of(NamedObject("stranger", size=100)) is None


class TestTheViewItself:
    def test_it_stores_no_matches(self):
        # The whole point: 800 members, and the object holds a list of members plus a descriptor.
        group = Group.from_identical(files(800))
        assert isinstance(group.matches, UniformMatches)
        assert len(group.matches) == 800 * 799 // 2

    def test_it_yields_every_pair_exactly_once(self):
        members = files(6)
        group = Group.from_identical(members)
        seen = [tuple(sorted((m.first.name, m.second.name))) for m in group.matches]
        assert len(seen) == len(set(seen)) == 15

    def test_membership_follows_the_group(self):
        members = files(4)
        group = Group.from_identical(members)
        inside = group.matches.match_between(members[0], members[1])
        assert inside in group.matches
        outside = Match(members[0], NamedObject("stranger", size=100), 100, kind=MatchKind.EXACT)
        assert outside not in group.matches

    def test_a_match_with_different_values_is_not_a_member(self):
        members = files(3)
        group = Group.from_identical(members, partial=False)
        assert Match(members[0], members[1], 100, partial=True, kind=MatchKind.EXACT) not in group.matches

    def test_an_empty_group_is_falsey(self):
        assert not bool(Group.from_identical(files(1)).matches)

    def test_materialising_gives_the_real_set(self):
        members = files(5)
        group = Group.from_identical(members)
        assert group.matches.materialise() == set(pair_built(members).matches)


class TestGroupingStillWorks:
    def test_get_groups_is_untouched_by_any_of_this(self):
        # Groups built from pairs must keep behaving exactly as before; nothing in the uniform
        # path should have leaked into the general one.
        a, b, c = files(3)
        groups = get_groups([Match(a, b, 100, kind=MatchKind.EXACT), Match(a, c, 100, kind=MatchKind.EXACT)])
        assert len(groups) == 1
        assert sorted(f.name for f in groups[0]) == ["f0", "f1"], "c matched only a, so it stays out"
        assert not groups[0].is_uniform
