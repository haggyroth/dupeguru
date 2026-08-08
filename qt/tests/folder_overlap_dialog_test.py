# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The folder overlap report (issue #127).

This dialog offers no actions, so what it has to get right is what it *says*. A percentage
without its counts invites the wrong decision -- "100% duplicated" over eleven files and over
eleven thousand are not the same situation -- and a folder listed without dupeGuru having
scanned it would be a percentage of the part we happened to look at.
"""

from pathlib import Path

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core.engine import Group, Match  # noqa: E402
from core.folder_overlap import FolderOverlap, count_files_per_folder  # noqa: E402
from core.results import Results  # noqa: E402
from core.tests.base import NamedObject  # noqa: E402
from hscommon.testutil import native  # noqa: E402
from qt.folder_overlap_dialog import (  # noqa: E402
    FolderOverlapDialog,
    describe_destinations,
    describe_redundancy,
)


def file_at(path):
    path = Path(path)
    return NamedObject(name=path.name, size=1000, folder=str(path.parent))


class FakeApp:
    def __init__(self, duplicates, extra_files=(), roots=()):
        files, groups = [], []
        for paths in duplicates:
            objs = [file_at(path) for path in paths]
            group = Group()
            for index, first in enumerate(objs):
                for second in objs[index + 1 :]:
                    group.add_match(Match(first, second, 100))
            groups.append(group)
            files.extend(objs)
        files.extend(file_at(path) for path in extra_files)

        results = Results(type("A", (), {"options": {}})())
        results.groups = groups
        self.model = type("M", (), {})()
        self.model.results = results
        self.model.folder_file_counts = count_files_per_folder(files, roots)


@pytest.fixture
def dialog_for(qapp):
    made = []

    def build(duplicates, extra_files=(), roots=()):
        dialog = FolderOverlapDialog(None, FakeApp(duplicates, extra_files, roots))
        made.append(dialog)
        return dialog

    yield build
    for dialog in made:
        dialog.close()


def rows(dialog):
    return {
        dialog.folderTree.topLevelItem(i).text(0): dialog.folderTree.topLevelItem(i)
        for i in range(dialog.folderTree.topLevelItemCount())
    }


class TestWhatItSays:
    def test_the_percentage_comes_with_the_counts_it_was_computed_from(self):
        # "100%" over eleven files and over eleven thousand invite very different decisions.
        overlap = FolderOverlap(
            "/backup", total_files=437, duplicated_files=437, destinations=[], other_destination_count=0
        )
        described = describe_redundancy(overlap)
        assert "100%" in described
        assert "437" in described

    def test_destinations_are_named_with_their_counts(self):
        from core.folder_overlap import Destination

        overlap = FolderOverlap("/backup", 100, 87, [Destination("/photos", 87)], other_destination_count=0)
        assert "/photos" in describe_destinations(overlap)
        assert "87" in describe_destinations(overlap)

    def test_extra_destinations_are_counted_rather_than_dropped(self):
        from core.folder_overlap import Destination

        overlap = FolderOverlap("/backup", 100, 87, [Destination("/photos", 40)], other_destination_count=3)
        assert "3 more" in describe_destinations(overlap)

    def test_a_folder_with_nowhere_else_shows_nothing_rather_than_a_stray_label(self):
        overlap = FolderOverlap("/backup", 100, 0, [], other_destination_count=0)
        assert describe_destinations(overlap) == ""


class TestListing:
    def test_each_overlapping_folder_gets_a_row(self, dialog_for):
        dialog = dialog_for([(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)])
        assert native("/backup") in rows(dialog)
        assert native("/photos") in rows(dialog)

    def test_the_most_redundant_folder_is_listed_first(self, dialog_for):
        dialog = dialog_for(
            [(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)],
            extra_files=[f"/photos/only{i}.jpg" for i in range(200)],
        )
        assert dialog.folderTree.topLevelItem(0).text(0) == native("/backup")

    def test_folders_duplicated_in_full_are_called_out(self, dialog_for):
        # A different statement from "mostly duplicated": these could in principle go entirely.
        dialog = dialog_for([(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)])
        assert "duplicated in full" in dialog.headerLabel.text()

    def test_a_scan_with_no_overlap_says_so(self, dialog_for):
        dialog = dialog_for([], extra_files=[f"/photos/only{i}.jpg" for i in range(30)])
        assert dialog.folderTree.topLevelItemCount() == 0
        assert "No folder" in dialog.headerLabel.text()

    def test_folders_outside_the_scan_are_absent(self, dialog_for):
        # Their percentage would be computed against only the files we happened to look at.
        dialog = dialog_for(
            [(f"/photos/img{i}.jpg", f"/Users/k/Downloads/img{i}.jpg") for i in range(20)],
            roots=["/photos", "/Users/k/Downloads"],
        )
        listed = rows(dialog)
        assert native("/Users/k/Downloads") in listed
        assert native("/Users") not in listed

    def test_the_report_offers_no_actions(self, dialog_for):
        # Understanding, not action: deciding happens in the results window and the rollup.
        dialog = dialog_for([(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)])
        labels = [button.text() for button in dialog.buttonBox.buttons()]
        assert labels == ["Close"]


class TestMissingCounts:
    def test_a_scan_that_recorded_no_counts_is_survivable(self, qapp):
        # Results loaded from a file have no scan behind them, so there are no folder totals
        # and no denominator. An empty report is the honest answer, not a crash.
        app = FakeApp([(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)])
        app.model.folder_file_counts = {}
        dialog = FolderOverlapDialog(None, app)
        assert dialog.folderTree.topLevelItemCount() == 0
        assert "No folder" in dialog.headerLabel.text()
        dialog.close()

    def test_an_app_without_the_attribute_at_all_is_survivable(self, qapp):
        app = FakeApp([(f"/photos/img{i}.jpg", f"/backup/img{i}.jpg") for i in range(20)])
        del app.model.folder_file_counts
        dialog = FolderOverlapDialog(None, app)
        assert dialog.folderTree.topLevelItemCount() == 0
        dialog.close()
