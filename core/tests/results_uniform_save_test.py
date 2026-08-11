# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Saving a group of identical files is linear in its members (issue #193).

The XML format records one ``<match>`` element per pair. For a group of k identical files that
is k(k-1)/2 elements to express what the members and three values already express -- about 17 GB
for the 23,857-file cluster in #180.

This was always true; what changed is that it became reachable. Before #192 a scan of such a
corpus exhausted memory and never got as far as saving.

The subtle part is not the writing, it is the reading. Omitting the matches without saying why
is *not* the same as recording them: the loader falls back to ``do_match``, which builds a
**name** match, so a 100% content duplicate comes back as whatever its filenames happen to score
and the partial flag is lost with it. So the group has to carry what it is, and the tests below
spend most of their attention on what comes back rather than on what goes out.
"""

import io
from xml.etree import ElementTree as ET

import pytest

from core.engine import Group, Match, MatchKind
from core.results import Results
from core.tests.base import NamedObject, TestApp


@pytest.fixture
def app():
    return TestApp().app


def identical(count, partial=False, kind=MatchKind.EXACT, percentage=100):
    members = [NamedObject(f"f{i}", size=100) for i in range(count)]
    return Group.from_identical(members, percentage, partial, kind)


def by_pairs(count, percentage=87):
    """A group built the ordinary way, which must keep writing its matches."""
    members = [NamedObject(f"g{i}", size=100) for i in range(count)]
    group = Group()
    for i, first in enumerate(members):
        for second in members[i + 1 :]:
            group.add_match(Match(first, second, percentage))
    return group


def save(app):
    buf = io.BytesIO()
    app.results.save_to_xml(buf)
    return buf.getvalue()


def reload(app, xml):
    results = Results(app)
    results.load_from_xml(io.BytesIO(xml), lambda path: NamedObject(path.split("/")[-1], size=100))
    return results


class TestWhatGetsWritten:
    def test_a_uniform_group_writes_no_match_elements(self, app):
        app.results.groups = [identical(6)]
        root = ET.fromstring(save(app))
        assert root.findall(".//match") == []
        assert root.find("group").get("uniform") == "y"

    def test_it_records_the_values_the_matches_carried(self, app):
        app.results.groups = [identical(4, partial=True, kind=MatchKind.EXACT, percentage=100)]
        group_elem = ET.fromstring(save(app)).find("group")
        assert group_elem.get("percentage") == "100"
        assert group_elem.get("partial") == "y"
        assert group_elem.get("kind") == MatchKind.EXACT

    def test_a_pair_built_group_still_writes_its_matches(self, app):
        # Only groups that are genuinely uniform can be described this way; everything else
        # keeps the format it had.
        app.results.groups = [by_pairs(4)]
        root = ET.fromstring(save(app))
        assert len(root.findall(".//match")) == 6
        assert root.find("group").get("uniform") is None

    def test_the_file_grows_linearly_with_the_group(self, app):
        sizes = {}
        for k in (20, 40, 80):
            app.results.groups = [identical(k)]
            sizes[k] = len(save(app))
        per_file = {k: size / k for k, size in sizes.items()}
        spread = max(per_file.values()) / min(per_file.values())
        assert spread < 1.2, f"cost per file is not flat: {per_file}"
        # Four times the files should cost about four times the bytes. The pair-based form would
        # cost about sixteen, since it writes k(k-1)/2 elements -- 190 against 3,160 here.
        assert sizes[80] < 6 * sizes[20], f"growth looks quadratic: {sizes}"


class TestWhatComesBack:
    def test_the_members_survive(self, app):
        app.results.groups = [identical(5)]
        reloaded = reload(app, save(app))
        assert len(reloaded.groups) == 1
        assert len(reloaded.groups[0]) == 5

    def test_the_percentage_survives(self, app):
        app.results.groups = [identical(4)]
        assert reload(app, save(app)).groups[0].percentage == 100

    def test_the_partial_flag_survives(self, app):
        # The dangerous one. A sampled-hash group that comes back as not-partial would be
        # reported as fully compared and would slip past the partial-match deletion gate.
        app.results.groups = [identical(4, partial=True)]
        group = reload(app, save(app)).groups[0]
        for dupe in group.dupes:
            match = group.get_match_of(dupe)
            assert match is not None and match.partial is True

    def test_the_kind_survives(self, app):
        app.results.groups = [identical(3, kind=MatchKind.EXACT)]
        group = reload(app, save(app)).groups[0]
        assert group.get_match_of(group.dupes[0]).kind == MatchKind.EXACT

    def test_it_comes_back_uniform_so_it_stays_cheap(self, app):
        # Otherwise loading a saved result would rebuild the quadratic set the save avoided.
        app.results.groups = [identical(30)]
        assert reload(app, save(app)).groups[0].is_uniform

    def test_a_round_trip_does_not_drift(self, app):
        app.results.groups = [identical(6, partial=True)]
        once = save(app)
        app.results.groups = reload(app, once).groups
        assert save(app) == once


class TestOlderAndOddFiles:
    def test_a_file_with_match_elements_still_loads(self, app):
        # Every results file written before this change. The uniform attribute is absent, so the
        # match elements are the record and must still be read as one.
        app.results.groups = [by_pairs(3, percentage=91)]
        reloaded = reload(app, save(app))
        group = reloaded.groups[0]
        assert len(group) == 3
        assert group.percentage == 91
        assert not group.is_uniform

    def test_a_group_with_neither_still_falls_back(self, app):
        # The pre-existing behaviour for a file that lists dupes and no matches at all: rebuild
        # by name. Unchanged, and deliberately not reached by the uniform path.
        xml = b"""<results>
          <group>
            <file path="/x/a" words="a" is_ref="n" marked="n"/>
            <file path="/x/b" words="a" is_ref="n" marked="n"/>
          </group>
        </results>"""
        group = reload(app, xml).groups[0]
        assert len(group) == 2
        assert not group.is_uniform

    def test_an_unknown_kind_is_read_as_the_weakest_claim(self, app):
        # Written by something newer, or hand-edited. Reading an unrecognised kind as exact
        # would let an unknown word license a bulk deletion.
        xml = b"""<results>
          <group uniform="y" percentage="100" partial="n" kind="teleported">
            <file path="/x/a" words="a" is_ref="n" marked="n"/>
            <file path="/x/b" words="a" is_ref="n" marked="n"/>
          </group>
        </results>"""
        group = reload(app, xml).groups[0]
        assert group.get_match_of(group.dupes[0]).kind == MatchKind.METADATA

    def test_a_malformed_percentage_does_not_lose_the_group(self, app):
        xml = b"""<results>
          <group uniform="y" percentage="not a number" partial="n" kind="exact">
            <file path="/x/a" words="a" is_ref="n" marked="n"/>
            <file path="/x/b" words="a" is_ref="n" marked="n"/>
          </group>
        </results>"""
        group = reload(app, xml).groups[0]
        assert len(group) == 2
        assert group.percentage == 100

    def test_a_uniform_group_with_one_file_is_not_resurrected(self, app):
        xml = b"""<results>
          <group uniform="y" percentage="100" partial="n" kind="exact">
            <file path="/x/a" words="a" is_ref="n" marked="n"/>
          </group>
        </results>"""
        assert reload(app, xml).groups == []
