# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Confidence triage against a real app: marking by tier, and the column (issue #124).

The classification itself is pinned down in confidence_test.py. What is at stake here is what
the classification is wired to -- a bulk mark aimed at a tier, and the tier appearing in the
results table -- because the point of the feature is that a bulk action can be aimed somewhere
defensible, and an action that marks the wrong files is worse than no action at all.
"""

import pytest

import cli
from core.app import AppMode, DupeGuru
from core.confidence import Confidence
from core.directories import DirectoryState
from core.scanner import ScanType


class _Silent:
    """Swallows every view callback.

    Marking notifies the GUI objects, and an unbound one raises. The CLI never notices because
    it marks through ``results`` directly, but ``mark_confidence`` is a GUI entry point and has
    to survive being called with the views attached -- which is the only way it is ever called.
    """

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def build(tmp_path, files):
    """Scan *files*, given as {relative path: bytes}."""
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    app = DupeGuru(view=cli._HeadlessView())
    app.app_mode = AppMode.STANDARD
    app.options["scan_type"] = ScanType.CONTENTS
    app.directories.add_path(tmp_path)
    app._recreate_result_table()
    for gui in (app.result_table, app.stats_label, app.details_panel, app.directory_tree):
        gui.view = _Silent()
        if hasattr(gui, "_columns"):
            gui._columns.view = _Silent()
    return app


@pytest.fixture
def mixed(tmp_path):
    """Two groups: one whose copies share a filename, one whose copies do not."""
    app = build(
        tmp_path,
        {
            "originals/report.pdf": b"report contents",
            "backup/report.pdf": b"report contents",
            "notes.txt": b"note contents",
            "notes-old.txt": b"note contents",
        },
    )
    cli._run_scan(app, verbose=False)
    assert len(app.results.groups) == 2, "fixture did not produce the expected results"
    app.results.mark_none()
    return app


def tier_of(app, name):
    """The tier of the group containing the file called *name*."""
    from core.confidence import classify_group

    for group in app.results.groups:
        if any(member.name == name for member in group):
            return classify_group(group).tier
    raise AssertionError(f"no group holds {name}")


class TestTheTally:
    def test_the_two_groups_land_in_different_tiers(self, mixed):
        # The fixture is only useful if it actually spans tiers; assert that before relying on it.
        assert tier_of(mixed, "report.pdf") == Confidence.CORROBORATED
        assert tier_of(mixed, "notes.txt") == Confidence.CONTENT

    def test_the_tally_counts_groups_not_files(self, mixed):
        counts = mixed.confidence_tally()
        assert counts[Confidence.CORROBORATED] == 1
        assert counts[Confidence.CONTENT] == 1
        assert counts[Confidence.UNCONFIRMED] == 0


class TestMarkingByTier:
    def test_marking_a_tier_marks_only_that_tier(self, mixed):
        marked = mixed.mark_confidence(Confidence.CORROBORATED)
        assert marked == 1
        names = {dupe.name for dupe in mixed.results.dupes if mixed.results.is_marked(dupe)}
        assert names == {"report.pdf"}

    def test_the_reference_of_a_group_is_never_marked(self, mixed):
        # Marking every member would offer to delete the copy being kept.
        assert mixed.mark_confidence(Confidence.CORROBORATED) == 1, "nothing was marked to check"
        for group in mixed.results.groups:
            assert not mixed.results.is_marked(group.ref)

    def test_marking_two_tiers_in_turn_leaves_both_marked(self, mixed):
        # Additive, so a user can build a selection one tier at a time and look before acting.
        # Replacing the selection would make the second click silently undo the first.
        mixed.mark_confidence(Confidence.CORROBORATED)
        mixed.mark_confidence(Confidence.CONTENT)
        assert mixed.results.mark_count == 2

    def test_marking_a_tier_twice_reports_nothing_new_the_second_time(self, mixed):
        assert mixed.mark_confidence(Confidence.CORROBORATED) == 1
        assert mixed.mark_confidence(Confidence.CORROBORATED) == 0

    def test_an_empty_tier_marks_nothing(self, mixed):
        assert mixed.mark_confidence(Confidence.UNCONFIRMED) == 0
        assert mixed.results.mark_count == 0

    def test_marking_does_not_disturb_marks_made_by_hand(self, mixed):
        # Whichever of the two notes files is not its group's reference -- which of them gets
        # promoted depends on the order the scan happened to collect them in.
        notes = next(g for g in mixed.results.groups if g.ref.name.startswith("notes"))
        by_hand = notes.dupes[0]
        mixed.results.mark(by_hand)
        mixed.mark_confidence(Confidence.CORROBORATED)
        assert mixed.results.is_marked(by_hand)

    def test_a_file_in_a_reference_folder_is_never_marked(self, tmp_path):
        # The folder that corroborated the group is the one that must survive it. dupeGuru
        # refuses to mark those files; this pins the guarantee to this entry point too.
        app = build(
            tmp_path,
            {"originals/report.pdf": b"report contents", "copies/report.pdf": b"report contents"},
        )
        app.directories.set_state(tmp_path / "originals", DirectoryState.REFERENCE)
        cli._run_scan(app, verbose=False)
        app.results.mark_none()
        assert tier_of(app, "report.pdf") == Confidence.CORROBORATED

        app.mark_confidence(Confidence.CORROBORATED)
        marked = [d for d in app.results.dupes if app.results.is_marked(d)]
        assert marked, "nothing was marked, so this proves nothing about what was spared"
        assert all("originals" not in str(dupe.path) for dupe in marked), "a Reference-folder file was marked"


class TestTheColumn:
    def test_every_row_of_a_group_shows_the_group_s_tier(self, mixed):
        # Including the reference row, and including power-marker mode where there is no
        # reference row to carry it.
        for group in mixed.results.groups:
            labels = {mixed.get_display_info(member, group)["confidence"] for member in group}
            assert len(labels) == 1, "one group showed two different tiers"

    def test_the_label_is_words_rather_than_the_internal_key(self, mixed):
        group = next(g for g in mixed.results.groups if any(m.name == "report.pdf" for m in g))
        assert mixed.get_display_info(group.ref, group)["confidence"] == "Corroborated"

    def test_a_missing_group_still_yields_a_confidence_cell(self, mixed):
        # get_display_info falls back to a full row of placeholders; a row missing this key
        # would raise in the table's delta comparison, which walks the dupe's keys.
        assert "confidence" in mixed.get_display_info(None, None)

    @pytest.mark.parametrize("mode", [AppMode.STANDARD, AppMode.MUSIC, AppMode.PICTURE])
    def test_every_mode_declares_the_column(self, mode):
        from core.me.result_table import ResultTable as MusicTable
        from core.pe.result_table import ResultTable as PictureTable
        from core.se.result_table import ResultTable as StandardTable

        tables = {
            AppMode.STANDARD: StandardTable,
            AppMode.MUSIC: MusicTable,
            AppMode.PICTURE: PictureTable,
        }
        assert "confidence" in [column.name for column in tables[mode].COLUMNS]
