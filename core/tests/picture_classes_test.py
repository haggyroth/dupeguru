# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Picture mode matches byte-identical files once, as a class, instead of pairwise.

The claim being tested is not "it is faster" but "it finds exactly what the pairwise route found",
so most of this file runs *both* routes over the same pictures -- through the real matcher, block
cache and grouping -- and compares group membership and every pair's percentage. The pairwise
route is reached by overriding the one hook the collapse uses, so nothing else differs.
"""

import io
import random
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

import pytest

from core import engine
from core.engine import ClassedMatches, Group, Match, MatchKind, UniformMatches
from core.pe import identical, matchblock
from core.pe.match_cache import MatchCache
from core.pe.photo import Photo
from core.pe.scanner import ScannerPE
from core.results import Results
from core.scanner import ScanType
from core.tests.base import NamedObject, TestApp

BLOCKS = 225
SIGNATURES = {}


def signature(shift, seed=0):
    """A 15x15 block signature that is *shift* away from the seed-0 one in every block."""
    rng = random.Random(1234 + seed)
    return [(rng.randint(0, 120) + shift, rng.randint(0, 120), rng.randint(0, 120)) for _ in range(BLOCKS)]


class FakePhoto(Photo):
    """A photo whose signature comes from a table, so no image decoder is needed."""

    def _plat_get_dimensions(self):
        return (100, 100)

    def _plat_get_blocks(self, block_count_per_side, orientation):
        return SIGNATURES[str(self.path)]


class Pairwise(ScannerPE):
    """The route before the collapse: no classes, every identical pair a match."""

    def _getmatches_classed(self, files, j):
        return None


def make(tmp_path, specs):
    """Photos for ``(name, bytes, shift)`` specs: the bytes are on disk, the shift is the signature."""
    photos = []
    for name, content, shift in specs:
        path = tmp_path / name
        path.write_bytes(content)
        SIGNATURES[str(path)] = signature(shift)
        photo = FakePhoto(path)
        photo.is_ref = False  # the scanner sets this; direct matcher calls need it too
        photos.append(photo)
    return photos


def scan(tmp_path, photos, scanner_class=ScannerPE, cache=None, **options):
    scanner = scanner_class()
    scanner.scan_type = ScanType.FUZZYBLOCK
    scanner.cache_path = str(tmp_path / f"blocks_{scanner_class.__name__}.db")
    scanner.min_match_percentage = options.pop("threshold", 80)
    scanner.match_cache = cache
    scanner.workers = 1
    for name, value in options.items():
        setattr(scanner, name, value)
    return scanner, scanner.get_dupe_groups(list(photos))


def describe(groups):
    """Everything observable about a result, independent of ordering: membership and each pair."""
    described = {}
    for group in groups:
        members = frozenset(str(f.path) for f in group)
        described[members] = {frozenset((str(m.first.path), str(m.second.path))): m.percentage for m in group.matches}
    return described


def same_result(tmp_path, photos, **options):
    """Run both routes and return ``(collapsed groups, pairwise groups)`` having asserted they agree."""
    _, collapsed = scan(tmp_path, photos, ScannerPE, **options)
    _, pairwise = scan(tmp_path, photos, Pairwise, **options)
    assert describe(collapsed) == describe(pairwise)
    return collapsed, pairwise


def content(tag):
    return f"picture {tag}".encode() * 50


class TestAClassOfIdenticalPictures:
    def test_one_class_is_one_group_with_every_pair_at_100(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(5)])
        collapsed, _ = same_result(tmp_path, photos)
        assert len(collapsed) == 1 and len(collapsed[0]) == 5
        assert {m.percentage for m in collapsed[0].matches} == {100}

    def test_it_is_not_materialised_pair_by_pair(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(40)])
        _, collapsed = scan(tmp_path, photos, ScannerPE)
        [group] = collapsed
        assert group.is_derived
        # A class nothing joined carries one descriptor rather than a table, and saves that way.
        assert group.is_uniform
        assert len(group.matches) == 40 * 39 // 2

    def test_the_matcher_sees_one_picture_not_forty(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(40)])
        matches, classes = matchblock.getmatches_classed(
            photos, str(tmp_path / "b.db"), 80, identical.Collapse(True, False), match_scaled=False
        )
        assert matches == []
        assert [len(c) for c in classes] == [40]
        pairwise = matchblock.getmatches(photos, str(tmp_path / "b2.db"), 80)
        assert len(pairwise) == 40 * 39 // 2, "the old route really was quadratic"

    def test_a_class_that_resembles_nothing_is_still_a_group(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("z.jpg", content("z"), 90)],
        )
        collapsed, _ = same_result(tmp_path, photos)
        assert [len(g) for g in collapsed] == [2]


class TestClassesAmongOtherPictures:
    def test_a_lone_picture_joins_the_class_it_resembles(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("s.jpg", content("s"), 4)],
        )
        collapsed, _ = same_result(tmp_path, photos)
        [group] = collapsed
        assert len(group) == 3

    def test_two_classes_that_resemble_each_other_stay_two_groups(self, tmp_path):
        # The pairwise route formed each identical set first, then skipped the match between two
        # files already in different groups. Merging them here would be a different answer.
        photos = make(
            tmp_path,
            [
                ("a0.jpg", content("a"), 0),
                ("a1.jpg", content("a"), 0),
                ("b0.jpg", content("b"), 6),
                ("b1.jpg", content("b"), 6),
            ],
        )
        collapsed, _ = same_result(tmp_path, photos)
        assert sorted(len(g) for g in collapsed) == [2, 2]

    def test_a_lone_picture_between_two_classes_joins_the_closer(self, tmp_path):
        photos = make(
            tmp_path,
            [
                ("a0.jpg", content("a"), 0),
                ("a1.jpg", content("a"), 0),
                ("b0.jpg", content("b"), 10),
                ("b1.jpg", content("b"), 10),
                ("s.jpg", content("s"), 4),
            ],
        )
        collapsed, _ = same_result(tmp_path, photos)
        sizes = sorted(len(g) for g in collapsed)
        assert sizes == [2, 3]

    def test_a_class_cannot_join_a_group_of_lookalikes(self, tmp_path):
        # Found by the random scenarios, and the reason groups are seeded with their classes.
        # The pairwise route had formed the identical set at 100% before it read the 91% matches,
        # so the set was already in a group and could not be absorbed by the two lookalikes. A
        # representative that was just a node in the collapsed matches could be.
        photos = make(
            tmp_path,
            [
                ("a0.jpg", content("a"), 3),
                ("a1.jpg", content("a"), 3),
                ("a2.jpg", content("a"), 3),
                ("l0.jpg", content("l0"), 12),
                ("l1.jpg", content("l1"), 12),
            ],
        )
        collapsed, _ = same_result(tmp_path, photos)
        assert sorted(len(g) for g in collapsed) == [2, 3]

    def test_the_percentage_between_classes_survives(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("s.jpg", content("s"), 7)],
        )
        collapsed, _ = same_result(tmp_path, photos)
        [group] = collapsed
        assert {m.percentage for m in group.matches} == {100, 93}

    def test_same_signature_but_different_bytes_is_99_not_100(self, tmp_path):
        # Equal signatures are a resemblance; "100%" has to mean the same bytes.
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("d.jpg", content("different"), 0)],
        )
        collapsed, _ = same_result(tmp_path, photos)
        [group] = collapsed
        assert {m.percentage for m in group.matches} == {100, 99}

    def test_pictures_below_the_threshold_are_not_grouped(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("far.jpg", content("far"), 60)],
        )
        collapsed, _ = same_result(tmp_path, photos)
        assert [len(g) for g in collapsed] == [2]

    def test_a_higher_threshold_changes_nothing_about_agreement(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("s.jpg", content("s"), 7)],
        )
        collapsed, _ = same_result(tmp_path, photos, threshold=97)
        assert [len(g) for g in collapsed] == [2]


class TestWhatIsNotCollapsed:
    def test_a_class_holding_a_reference_file_keeps_the_pairwise_route(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(3)])
        photos[0].is_ref = True
        collapsed, _ = same_result(tmp_path, photos)
        assert collapsed and not any(g.is_derived for g in collapsed)

    def test_an_ignore_list_keeps_the_pairwise_route(self, tmp_path):
        from core.ignore import IgnoreList

        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(3)])
        ignore = IgnoreList()
        ignore.ignore(str(photos[0].path), str(photos[1].path))
        scanner = ScannerPE()
        scanner.scan_type = ScanType.FUZZYBLOCK
        scanner.cache_path = str(tmp_path / "ig.db")
        scanner.workers = 1
        pairwise = Pairwise()
        pairwise.scan_type = ScanType.FUZZYBLOCK
        pairwise.cache_path = str(tmp_path / "ig2.db")
        pairwise.workers = 1
        assert describe(scanner.get_dupe_groups(list(photos), ignore)) == describe(
            pairwise.get_dupe_groups(list(photos), ignore)
        )

    def test_files_of_different_extensions_do_not_share_a_class_when_kinds_are_not_mixed(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("a2.png", content("a"), 0)],
        )
        collapsed, _ = same_result(tmp_path, photos, mix_file_kind=False)
        assert [len(g) for g in collapsed] == [2]

    def test_they_do_when_kinds_are_mixed(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("a2.png", content("a"), 0)],
        )
        collapsed, _ = same_result(tmp_path, photos, mix_file_kind=True)
        assert [len(g) for g in collapsed] == [3]

    def test_a_missing_file_is_dropped_from_its_class(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(4)])
        # Loaded before the file goes, as a real scan has loaded size at collection time and the
        # digest earlier: a file with neither never joins a class, which would make this test pass
        # for the wrong reason.
        for photo in photos:
            photo.size
            photo.digest
        photos[1].path.unlink()
        collapsed, _ = same_result(tmp_path, photos, include_exists_check=True)
        assert [len(g) for g in collapsed] == [3]
        assert not any(str(photos[1].path) == str(f.path) for g in collapsed for f in g)

    def test_a_picture_with_no_orientation_zero_signature_is_left_alone(self, tmp_path):
        # The matcher never compared such a picture, so grouping its copies would invent groups.
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(3)])
        for photo in photos:
            photo._cached_orientation = 6  # stored at a different orientation slot
        collapsed, _ = same_result(tmp_path, photos)
        assert collapsed == []


class TestIdenticalClasses:
    def pics(self, specs):
        class P:
            def __init__(self, name, size, signature, digest, is_ref=False, exists=True):
                self.name, self.size, self.sig, self.digest = name, size, signature, digest
                self.is_ref, self._exists = is_ref, exists

            def exists(self):
                return self._exists

        return [P(*spec) for spec in specs]

    OPTIONS = identical.Collapse(True, False)

    def sig(self, p):
        return p.sig

    def test_same_size_signature_and_digest_make_a_class(self):
        ps = self.pics([("a", 10, "s", b"d"), ("b", 10, "s", b"d"), ("c", 10, "s", b"e")])
        [cls] = identical.identical_classes(ps, self.sig, self.OPTIONS)
        assert [p.name for p in cls] == ["a", "b"]

    def test_a_different_size_is_not_compared(self):
        ps = self.pics([("a", 10, "s", b"d"), ("b", 11, "s", b"d")])
        assert identical.identical_classes(ps, self.sig, self.OPTIONS) == []

    def test_no_digest_means_not_known_to_be_identical(self):
        ps = self.pics([("a", 10, "s", b""), ("b", 10, "s", b"")])
        assert identical.identical_classes(ps, self.sig, self.OPTIONS) == []

    def test_no_signature_takes_no_part(self):
        ps = self.pics([("a", 10, None, b"d"), ("b", 10, None, b"d")])
        assert identical.identical_classes(ps, self.sig, self.OPTIONS) == []

    def test_a_reference_member_keeps_the_class_out(self):
        ps = self.pics([("a", 10, "s", b"d", True), ("b", 10, "s", b"d")])
        assert identical.identical_classes(ps, self.sig, self.OPTIONS) == []

    def test_digests_are_only_read_inside_a_bucket(self):
        reads = []

        class P:
            def __init__(self, name, size, sig):
                self.name, self.size, self.sig, self.is_ref = name, size, sig, False

            @property
            def digest(self):
                reads.append(self.name)
                return b"d"

        ps = [P("a", 10, "s1"), P("b", 10, "s1"), P("lonely", 10, "s2"), P("other", 11, "s1")]
        identical.identical_classes(ps, lambda p: p.sig, self.OPTIONS)
        assert sorted(reads) == ["a", "b"]

    def test_representatives_keep_order_and_drop_only_the_extras(self):
        ps = self.pics([("a", 10, "s", b"d"), ("x", 5, "t", b"x"), ("b", 10, "s", b"d"), ("c", 10, "s", b"d")])
        classes = identical.identical_classes(ps, self.sig, self.OPTIONS)
        assert [p.name for p in identical.representatives(ps, classes)] == ["a", "x"]


class TestRandomScenarios:
    """Many small random corpora, compared route against route. Where the pairwise route is
    arbitrary (equal percentages competing for a picture) both are free to differ, so the shifts
    are spaced to keep every comparison unambiguous."""

    @pytest.mark.parametrize("seed", range(25))
    def test_both_routes_agree(self, tmp_path, seed):
        rng = random.Random(seed)
        clusters = rng.randint(2, 5)
        # Spacing of 3 shift units between clusters keeps cross-cluster percentages distinct.
        shifts = sorted(rng.sample(range(0, 40, 3), clusters))
        specs = []
        for index, shift in enumerate(shifts):
            copies = rng.choice([1, 1, 2, 3, 5])
            for copy in range(copies):
                specs.append((f"c{index}_{copy}.jpg", content(f"cluster{index}"), shift))
            if rng.random() < 0.4:
                # A lookalike: same signature, other bytes.
                specs.append((f"c{index}_x.jpg", content(f"lookalike{index}"), shift))
        rng.shuffle(specs)
        photos = make(tmp_path, specs)
        same_result(tmp_path, photos)


class TestGetGroupsWithPreformedGroups:
    def seed(self, *files):
        group = Group()
        group.ordered = list(files)
        group.unordered = set(files)
        return group

    def objs(self, *names):
        return [NamedObject(n, size=1) for n in names]

    def test_no_preformed_groups_is_the_old_behaviour(self):
        a, b = self.objs("a", "b")
        [group] = engine.get_groups([Match(a, b, 90)])
        assert {a, b} == group.unordered

    def test_a_file_in_a_preformed_group_is_not_absorbed_by_another(self):
        a, l0, l1 = self.objs("a", "l0", "l1")
        matches = [Match(l0, l1, 99), Match(a, l0, 91), Match(a, l1, 91)]
        groups = engine.get_groups(matches, preformed=[self.seed(a)])
        assert sorted(len(g) for g in groups) == [1, 2]

    def test_a_lone_file_can_join_a_preformed_group_it_matches(self):
        a, s = self.objs("a", "s")
        [group] = engine.get_groups([Match(a, s, 96)], preformed=[self.seed(a)])
        assert group.unordered == {a, s}

    def test_two_preformed_groups_never_merge(self):
        a, b = self.objs("a", "b")
        groups = engine.get_groups([Match(a, b, 99)], preformed=[self.seed(a), self.seed(b)])
        assert sorted(len(g) for g in groups) == [1, 1]

    def test_a_lone_file_must_match_every_member_to_join(self):
        a, x, s = self.objs("a", "x", "s")
        # s matches a but not x, and a and x are already together
        groups = engine.get_groups([Match(a, s, 95)], preformed=[self.seed(a, x)])
        assert sorted(len(g) for g in groups) == [2]

    def test_preformed_groups_come_back_even_when_nothing_matched(self):
        a, b = self.objs("a", "b")
        groups = engine.get_groups([], preformed=[self.seed(a), self.seed(b)])
        assert len(groups) == 2


class TestGroupFromClasses:
    def group(self):
        a = [NamedObject(f"a{i}", size=10) for i in range(3)]
        s = [NamedObject("s", size=10)]
        b = [NamedObject(f"b{i}", size=10) for i in range(2)]
        return Group.from_classes([a, s, b], {(0, 1): 96, (0, 2): 90, (1, 2): 94}), a, s[0], b

    def test_every_pair_is_there_with_the_right_percentage(self):
        group, a, s, b = self.group()
        pairs = {frozenset((m.first.name, m.second.name)): m.percentage for m in group.matches}
        assert len(pairs) == 6 * 5 // 2
        assert pairs[frozenset(("a0", "a1"))] == 100
        assert pairs[frozenset(("a0", "s"))] == 96
        assert pairs[frozenset(("s", "b1"))] == 94
        assert pairs[frozenset(("a2", "b0"))] == 90

    def test_it_is_derived_but_not_uniform(self):
        group, *_ = self.group()
        assert group.is_derived and not group.is_uniform
        assert isinstance(group.matches, ClassedMatches)

    def test_length_and_truth(self):
        group, *_ = self.group()
        assert len(group.matches) == 15 and group.matches

    def test_membership_of_a_match(self):
        group, a, s, b = self.group()
        assert Match(a[0], s, 96, kind=MatchKind.RESEMBLANCE) in group.matches
        assert Match(a[0], s, 95, kind=MatchKind.RESEMBLANCE) not in group.matches
        assert Match(a[0], a[1], 100, kind=MatchKind.RESEMBLANCE) in group.matches

    def test_match_between_and_matches_for(self):
        group, a, s, b = self.group()
        assert group.matches.match_between(a[0], b[1]).percentage == 90
        assert sorted(m.percentage for m in group.matches.matches_for(s)) == [94, 94, 96, 96, 96]

    def test_the_group_percentage_is_the_mean_for_its_reference(self):
        group, a, s, b = self.group()
        # ref is a0: two at 100, s at 96, two at 90 -> (100+100+96+90+90)//5
        assert group.percentage == (100 + 100 + 96 + 90 + 90) // 5

    def test_get_match_of_answers_for_a_member(self):
        group, a, s, b = self.group()
        assert group.get_match_of(s).percentage == 96
        assert group.get_match_of(a[0]) is None

    def test_switching_the_reference_changes_what_is_derived(self):
        group, a, s, b = self.group()
        group.switch_ref(s)
        assert group.get_match_of(a[0]).percentage == 96
        assert group.percentage == (96 * 3 + 94 * 2) // 5

    def test_removing_a_member_keeps_the_rest(self):
        group, a, s, b = self.group()
        group.remove_dupe(a[2])
        assert len(group) == 5 and len(group.matches) == 10
        assert a[2] not in group
        assert group.get_match_of(s).percentage == 96

    def test_removing_down_to_one_clears_the_group(self):
        group, a, s, b = self.group()
        for member in [*a[1:], *b, s]:
            group.remove_dupe(member)
        assert len(group) == 0

    def test_discard_matches_has_nothing_to_discard(self):
        group, *_ = self.group()
        assert group.discard_matches() == set()

    def test_a_pair_of_classes_with_no_match_is_refused(self):
        a, b = [NamedObject("a", size=1)], [NamedObject("b", size=1)]
        with pytest.raises(ValueError):
            Group.from_classes([a, b], {})

    def test_classes_and_percentages_describe_the_group_for_saving(self):
        group, a, s, b = self.group()
        classes = group.matches.classes()
        assert [[m.name for m in c] for c in classes] == [["a0", "a1", "a2"], ["s"], ["b0", "b1"]]
        assert group.matches.class_percentages() == [(0, 1, 96), (0, 2, 90), (1, 2, 94)]

    def test_after_removing_a_whole_class_positions_still_line_up(self):
        group, a, s, b = self.group()
        for member in a:
            group.remove_dupe(member)
        classes = group.matches.classes()
        assert [[m.name for m in c] for c in classes] == [["s"], ["b0", "b1"]]
        assert group.matches.class_percentages() == [(0, 1, 94)]

    def test_a_uniform_group_is_still_uniform(self):
        group = Group.from_identical([NamedObject("x", size=1), NamedObject("y", size=1)])
        assert group.is_uniform and group.is_derived and isinstance(group.matches, UniformMatches)


class TestSavingAndLoading:
    @pytest.fixture
    def app(self):
        return TestApp().app

    def round_trip(self, app, group):
        app.results.groups = [group]
        buf = io.BytesIO()
        app.results.save_to_xml(buf)
        xml = buf.getvalue()
        reloaded = Results(app)
        reloaded.load_from_xml(
            io.BytesIO(xml),
            lambda path: NamedObject(PurePosixPath(path.replace("\\", "/")).name, size=10),
        )
        return xml, reloaded

    def classed_group(self):
        a = [NamedObject(f"a{i}", size=10) for i in range(40)]
        s = [NamedObject("s", size=10)]
        b = [NamedObject(f"b{i}", size=10) for i in range(30)]
        return Group.from_classes([a, s, b], {(0, 1): 96, (0, 2): 90, (1, 2): 94})

    def test_the_file_does_not_hold_a_match_per_pair(self, app):
        xml, _ = self.round_trip(app, self.classed_group())
        root = ET.fromstring(xml)
        assert root.find("group").get("classed") == "y"
        assert len(root.findall(".//match")) == 3, "one per pair of classes, not per pair of files"
        assert len(root.findall(".//class")) == 2

    def test_reloading_gives_back_every_pair(self, app):
        original = self.classed_group()
        _, reloaded = self.round_trip(app, original)
        [group] = reloaded.groups
        assert group.is_derived
        pairs = lambda g: {frozenset((m.first.name, m.second.name)): m.percentage for m in g.matches}  # noqa: E731
        assert pairs(group) == pairs(original)

    def test_the_reloaded_percentage_matches(self, app):
        original = self.classed_group()
        _, reloaded = self.round_trip(app, original)
        assert reloaded.groups[0].percentage == original.percentage

    def test_a_missing_file_leaves_the_rest_intact(self, app):
        original = self.classed_group()
        app.results.groups = [original]
        buf = io.BytesIO()
        app.results.save_to_xml(buf)
        reloaded = Results(app)
        reloaded.load_from_xml(
            io.BytesIO(buf.getvalue()),
            lambda path: (
                None if path.endswith("a3") else NamedObject(PurePosixPath(path.replace("\\", "/")).name, size=10)
            ),
        )
        [group] = reloaded.groups
        assert len(group) == 70
        assert group.get_match_of(next(f for f in group if f.name == "s")) is not None

    def test_a_match_naming_its_classes_the_other_way_round_still_loads(self, app):
        original = self.classed_group()
        app.results.groups = [original]
        buf = io.BytesIO()
        app.results.save_to_xml(buf)
        root = ET.fromstring(buf.getvalue())
        for match in root.findall(".//match"):
            first, second = match.get("first"), match.get("second")
            match.set("first", second)
            match.set("second", first)
        reloaded = Results(app)
        reloaded.load_from_xml(
            io.BytesIO(ET.tostring(root)),
            lambda path: NamedObject(PurePosixPath(path.replace("\\", "/")).name, size=10),
        )
        [group] = reloaded.groups
        assert group.is_derived
        assert group.percentage == original.percentage

    def test_a_group_of_plain_pairs_is_unchanged(self, app):
        members = [NamedObject(f"g{i}", size=10) for i in range(3)]
        group = Group()
        for i, first in enumerate(members):
            for second in members[i + 1 :]:
                group.add_match(Match(first, second, 87))
        xml, reloaded = self.round_trip(app, group)
        assert ET.fromstring(xml).find("group").get("classed") is None
        assert len(reloaded.groups[0].matches) == 3


class TestTheMatchCacheKeepsClasses:
    def test_a_rescan_is_served_from_the_cache_with_its_classes(self, tmp_path):
        photos = make(
            tmp_path,
            [("a0.jpg", content("a"), 0), ("a1.jpg", content("a"), 0), ("s.jpg", content("s"), 5)],
        )
        cache = MatchCache()
        cache.connect(str(tmp_path / "pm.db"))
        _, first = scan(tmp_path, photos, ScannerPE, cache=cache)
        # A second scan must not decode or match anything: break the block source to prove it.
        for photo in photos:
            SIGNATURES[str(photo.path)] = None
        _, second = scan(tmp_path, photos, ScannerPE, cache=cache)
        assert describe(second) == describe(first)
        cache.close()

    def test_everything_collapsing_into_classes_still_caches(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(4)])
        cache = MatchCache()
        cache.connect(str(tmp_path / "pm2.db"))
        scanner, _ = scan(tmp_path, photos, ScannerPE, cache=cache)
        key = matchblock.match_cache_module.compute_key(
            photos,
            80,
            False,
            False,
            extra=repr(identical.Collapse(scanner.mix_file_kind, scanner.include_exists_check)),
        )
        cached = cache.get_with_classes(key, photos)
        assert cached is not None, "an empty match set with classes is a real, storable result"
        matches, classes = cached
        assert matches == [] and [len(c) for c in classes] == [4]
        cache.close()

    def test_the_collapse_options_are_part_of_the_key(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(2)])
        from core.pe import match_cache

        one = match_cache.compute_key(photos, 80, False, False, extra=repr(identical.Collapse(True, False)))
        two = match_cache.compute_key(photos, 80, False, False, extra=repr(identical.Collapse(False, False)))
        none = match_cache.compute_key(photos, 80, False, False)
        assert len({one, two, none}) == 3

    def test_storing_a_new_key_drops_the_old_classes(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(3)])
        cache = MatchCache()
        cache.connect(str(tmp_path / "pm4.db"))
        cache.put("old", [], [photos])
        cache.put("new", [], [photos[:2]])
        assert cache.get_with_classes("old", photos) is None
        assert cache.con.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 2
        cache.close()

    def test_a_cache_entry_naming_a_missing_class_member_is_a_miss(self, tmp_path):
        photos = make(tmp_path, [(f"a{i}.jpg", content("a"), 0) for i in range(3)])
        cache = MatchCache()
        cache.connect(str(tmp_path / "pm3.db"))
        cache.put("k", [], [photos])
        assert cache.get_with_classes("k", photos[:2]) is None
        cache.close()
