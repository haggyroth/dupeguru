# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""`DupeGuru.deletion_preview` -- what the GUI's preview is built on (issue #131).

The CLI's `--plan` marks everything and plans that. The GUI must plan *the user's marks*
instead, and the two share one implementation, so the marking boundary is the thing most
likely to be got wrong and the thing most worth pinning down here.

The other property under test is the one the whole feature rests on: the preview and the
deletion consult the same predicate, so the preview cannot promise a deletion that is then
refused, or refuse one that then happens.
"""

import os

import pytest

import cli
from core.app import AppMode, DupeGuru
from core.deletion_plan import DeletionPlan, summarize_plan
from core.scanner import ScanType


@pytest.fixture
def scanned(tmp_path):
    """A real app holding real results for four files forming two groups."""
    for name, content in [("a.txt", b"same"), ("b.txt", b"same"), ("c.txt", b"other"), ("d.txt", b"other")]:
        (tmp_path / name).write_bytes(content)
    app = DupeGuru(view=cli._HeadlessView())
    app.app_mode = AppMode.STANDARD
    app.options["scan_type"] = ScanType.CONTENTS
    app.directories.add_path(tmp_path)
    cli._run_scan(app, verbose=False)
    assert len(app.results.groups) == 2, "fixture did not produce the expected results"
    app.results.mark_none()
    return app


def marked_dupes(app):
    return [d for g in app.results.groups for d in g.dupes]


class TestPlansTheUserSMarks:
    """Marking is the caller's business; the preview must not widen or narrow it."""

    def test_nothing_marked_plans_nothing(self, scanned):
        plan = scanned.deletion_preview()
        assert plan.files == 0
        assert plan.groups == 0
        assert plan.total_bytes == 0

    def test_one_marked_file_plans_exactly_that_file(self, scanned):
        target = marked_dupes(scanned)[0]
        scanned.results.mark(target)
        plan = scanned.deletion_preview()
        assert plan.files == 1
        assert plan.groups == 1
        planned = [d["path"] for e in plan.entries for d in e["duplicates"]]
        assert planned == [str(target.path)]

    def test_marking_everything_plans_everything(self, scanned):
        scanned.results.mark_all()
        assert scanned.deletion_preview().files == len(marked_dupes(scanned))

    def test_previewing_does_not_disturb_the_marks(self, scanned):
        # The preview is a read. If it marked or unmarked anything, the user's selection
        # would change under them just for having looked at it.
        target = marked_dupes(scanned)[0]
        scanned.results.mark(target)
        before = scanned.results.mark_count
        scanned.deletion_preview()
        assert scanned.results.mark_count == before
        assert scanned.results.is_marked(target)

    def test_previewing_deletes_nothing(self, scanned, tmp_path):
        scanned.results.mark_all()
        scanned.deletion_preview()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt", "c.txt", "d.txt"]


class TestPreviewAgreesWithDeletion:
    def test_a_file_changed_since_the_scan_is_previewed_as_blocked(self, scanned):
        # The same staleness check that makes the deleter refuse the file has to make the
        # preview report it. Reachable in the GUI any time results sit on screen a while.
        target = marked_dupes(scanned)[0]
        scanned.results.mark(target)
        with open(target.path, "wb") as fp:
            fp.write(b"changed underneath")
        plan = scanned.deletion_preview()
        assert plan.files == 0, "a changed file must not be promised as deletable"
        assert sum(plan.blocked.values()) == 1
        assert plan.blocked_bytes > 0

    def test_a_deleted_file_is_previewed_as_blocked(self, scanned):
        target = marked_dupes(scanned)[0]
        scanned.results.mark(target)
        os.remove(target.path)
        plan = scanned.deletion_preview()
        assert plan.files == 0
        assert sum(plan.blocked.values()) == 1

    def test_clones_are_not_reported_unless_the_user_asked_for_them(self, scanned):
        # Probing costs a filesystem test per candidate, and answers a question nobody asked
        # when the option is off.
        scanned.results.mark_all()
        assert scanned.deletion_preview().cloneable == 0


class TestSummaryWording:
    """The GUI and the CLI describe an identical plan identically, by construction."""

    def _plan(self, **kwargs):
        fields = {
            "groups": 2,
            "files": 4,
            "total_bytes": 2048,
            "partial": 0,
            "full_content": 4,
            "blocked": {},
            "blocked_bytes": 0,
            "cross_volume": 0,
            "cloneable": 0,
            "confidence": {},
            "entries": [],
        }
        fields.update(kwargs)
        return DeletionPlan(**fields)

    def test_trash_and_permanent_deletion_are_worded_differently(self):
        # Asked for rather than spelled out: the trashing verb names the Recycle Bin on Windows
        # and the trash elsewhere (#215), so a literal here would pass on one platform only.
        from core.trash import deletion_verb

        assert deletion_verb(False) in summarize_plan(self._plan(), direct_delete=False)[0]
        assert "permanently delete" in summarize_plan(self._plan(), direct_delete=True)[0]
        assert deletion_verb(False) != deletion_verb(True)

    def test_a_clean_plan_says_nothing_about_skips_or_clones(self):
        lines = summarize_plan(self._plan())
        assert not any("skipped" in line for line in lines)
        assert not any("clone" in line for line in lines)

    def test_the_partial_hint_is_the_front_end_s_to_supply(self):
        # The CLI can name a flag; the GUI cannot. Baking either into core would put a
        # command-line flag in a dialog or drop the advice from the terminal.
        lines = summarize_plan(self._plan(partial=1), partial_hint=" and would be refused without --x")
        assert any("--x" in line for line in lines)
        assert not any("--x" in line for line in summarize_plan(self._plan(partial=1)))

    def test_cloneable_files_are_reported(self):
        lines = summarize_plan(self._plan(cloneable=3))
        assert any("3 could be replaced by a copy-on-write clone" in line for line in lines)
