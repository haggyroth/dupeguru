# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""How redundant each folder is (issue #127).

The number this reports is a percentage of a folder's *whole* content, which makes its
denominator the thing most worth guarding. Results hold only duplicates, so the count has to
come from the scan -- and it has to stop at the scanned roots, because a percentage computed
against only the part we happened to look at is not approximate, it is wrong.
"""

from pathlib import Path

import pytest

from core.engine import Group, Match
from core.folder_overlap import (
    MAX_DESTINATIONS,
    build_overlaps,
    count_files_per_folder,
)
from core.results import Results
from core.tests.base import NamedObject


def native(path):
    """A folder string as this platform writes it."""
    return str(Path(path))


def file_at(path, size=1000):
    path = Path(path)
    return NamedObject(name=path.name, size=size, folder=str(path.parent))


class Scan:
    """A scan: every file collected, and the duplicate groups found among them."""

    def __init__(self):
        self.files = []
        self.groups = []

    def duplicate(self, *paths):
        """A group whose members all duplicate each other.

        Every pair, not just every pair with the first: Group.add_match only admits a file that
        matches *all* the others, so linking a third member only to the first leaves it out of
        the group entirely -- a fixture that silently builds two members while claiming three.
        """
        objs = [file_at(path) for path in paths]
        group = Group()
        for i, first in enumerate(objs):
            for second in objs[i + 1 :]:
                group.add_match(Match(first, second, 100))
        assert len(list(group)) == len(objs), "the fixture did not build the group it claims"
        self.groups.append(group)
        self.files.extend(objs)
        return self

    def unique(self, path, count=1):
        for index in range(count):
            self.files.append(file_at(f"{path}/unique{index}.dat"))
        return self

    def overlaps(self, roots=(), min_files=1):
        results = Results(type("A", (), {"options": {}})())
        results.groups = self.groups
        return build_overlaps(results, count_files_per_folder(self.files, roots), min_files=min_files)

    def by_folder(self, folder, **kwargs):
        wanted = native(folder)
        for overlap in self.overlaps(**kwargs):
            if overlap.folder == wanted:
                return overlap
        return None


class TestRedundancy:
    def test_a_folder_wholly_duplicated_elsewhere_reads_as_complete(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        overlap = scan.by_folder("/backup")
        assert overlap.redundancy == 1.0
        assert overlap.is_wholly_redundant

    def test_unique_files_dilute_the_percentage(self):
        # The whole reason the denominator is the folder's content rather than its duplicates.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        scan.unique("/backup", count=80)
        overlap = scan.by_folder("/backup")
        assert overlap.total_files == 100
        assert overlap.duplicated_files == 20
        assert overlap.redundancy == pytest.approx(0.2)
        assert not overlap.is_wholly_redundant

    def test_both_sides_of_a_relationship_are_described(self):
        # "/backup is 87% redundant" and "/photos is 13% redundant" are different facts, and
        # which matters depends on what the user is working out.
        scan = Scan()
        for i in range(87):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        scan.unique("/backup", count=13)  # /backup: 87 of 100
        scan.unique("/photos", count=613)  # /photos: 87 of 700
        assert scan.by_folder("/backup").redundancy == pytest.approx(0.87, abs=0.01)
        assert scan.by_folder("/photos").redundancy == pytest.approx(0.124, abs=0.01)

    def test_a_file_duplicated_in_several_places_counts_once_for_its_folder(self):
        # A file matching two other copies is still one redundant file. Counting its matches
        # instead would report 20 of 10, and the clamp would quietly turn that into 100% --
        # so the folder needs room for the inflated number to show, or the bug hides.
        scan = Scan()
        for i in range(10):
            scan.duplicate(f"/backup/img{i}.jpg", f"/photos/img{i}.jpg", f"/archive/img{i}.jpg")
        scan.unique("/backup", count=30)
        overlap = scan.by_folder("/backup")
        assert overlap.total_files == 40
        assert overlap.duplicated_files == 10, "matches were counted instead of files"
        assert overlap.redundancy == pytest.approx(0.25)

    def test_the_most_redundant_folder_comes_first(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        scan.unique("/photos", count=200)
        assert scan.overlaps()[0].folder == native("/backup")


class TestDenominator:
    def test_counting_stops_at_the_scanned_roots(self):
        # Scanning /Users/k/Downloads says nothing about /Users. Reporting a percentage for it
        # would divide by the handful of files we looked at, not the folder's real contents.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/Users/k/Downloads/img{i}.jpg")
        folders = [overlap.folder for overlap in scan.overlaps(roots=["/photos", "/Users/k/Downloads"])]
        assert native("/Users/k/Downloads") in folders
        assert native("/Users") not in folders
        assert native("/Users/k") not in folders

    def test_a_root_itself_is_still_reported(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        folders = [overlap.folder for overlap in scan.overlaps(roots=["/photos", "/backup"])]
        assert native("/backup") in folders

    def test_counts_are_taken_at_every_depth_within_a_root(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/2023/img{i}.jpg", f"/backup/2023/img{i}.jpg")
        totals = count_files_per_folder(scan.files, ["/photos", "/backup"])
        assert totals[native("/photos")] == 20
        assert totals[native("/photos/2023")] == 20

    def test_the_filesystem_root_is_never_reported(self):
        # "/ is 22% redundant" is arithmetically true and describes nothing.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        assert all(overlap.folder != native("/") for overlap in scan.overlaps())


class TestDestinations:
    def test_where_the_content_also_lives_is_named(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        overlap = scan.by_folder("/backup")
        assert [dest.folder for dest in overlap.destinations] == [native("/photos")]
        assert overlap.destinations[0].file_count == 20

    def test_a_destination_covered_by_a_deeper_one_is_dropped(self):
        # /photos/2023 (20) and /photos (20) are the same twenty files at two depths. Only the
        # innermost says anything the other does not.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/2023/img{i}.jpg", f"/backup/img{i}.jpg")
        folders = [dest.folder for dest in scan.by_folder("/backup").destinations]
        assert native("/photos/2023") in folders
        assert native("/photos") not in folders

    def test_a_parent_holding_more_than_the_child_is_kept(self):
        # Here /photos genuinely matches more than /photos/2023 does, so it is not the same
        # statement and dropping it would lose information.
        scan = Scan()
        for i in range(10):
            scan.duplicate(f"/photos/2023/img{i}.jpg", f"/backup/img{i}.jpg")
        for i in range(10):
            scan.duplicate(f"/photos/2024/img{i}.jpg", f"/backup/other{i}.jpg")
        folders = [dest.folder for dest in scan.by_folder("/backup").destinations]
        assert native("/photos") in folders

    def test_the_list_is_truncated_and_the_remainder_counted(self):
        scan = Scan()
        for i in range(MAX_DESTINATIONS + 3):
            for n in range(3):
                scan.duplicate(f"/dest{i}/img{n}.jpg", f"/backup/d{i}n{n}.jpg")
        overlap = scan.by_folder("/backup")
        assert len(overlap.destinations) == MAX_DESTINATIONS
        assert overlap.other_destination_count > 0

    def test_a_duplicate_inside_the_same_folder_is_not_an_elsewhere(self):
        # Two copies in one folder say nothing about that folder overlapping another.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/photos/img{i} copy.jpg")
        overlap = scan.by_folder("/photos")
        assert overlap.destinations == []


class TestQuietWhereThereIsNothingToSay:
    def test_tiny_folders_are_not_described(self):
        # "100% redundant" over two files says nothing about the shape of an archive.
        scan = Scan()
        for i in range(2):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        assert scan.overlaps(min_files=10) == []

    def test_a_folder_with_no_duplicates_is_omitted(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg")
        scan.unique("/untouched", count=50)
        assert all(overlap.folder != native("/untouched") for overlap in scan.overlaps())

    def test_a_folder_whose_parent_says_the_same_thing_is_dropped(self):
        # /backup holding nothing but /backup/2023 gives both identical counts; listing both
        # says the same thing twice.
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/2023/img{i}.jpg")
        folders = [overlap.folder for overlap in scan.overlaps()]
        assert native("/backup") in folders
        assert native("/backup/2023") not in folders

    def test_a_parent_with_more_content_than_its_child_is_kept(self):
        scan = Scan()
        for i in range(20):
            scan.duplicate(f"/photos/img{i}.jpg", f"/backup/2023/img{i}.jpg")
        scan.unique("/backup", count=30)
        folders = [overlap.folder for overlap in scan.overlaps()]
        assert native("/backup") in folders
        assert native("/backup/2023") in folders

    def test_an_empty_scan_produces_nothing(self):
        assert Scan().overlaps() == []
