# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The GUI deletion preview (issue #131).

The point of the preview is that it cannot lie about what the deletion will do. It gets that
property by presenting `core.deletion_plan`'s output rather than computing anything -- so what
is worth guarding here is the presentation staying faithful to the plan it was handed, and the
dialog surviving the shapes a real plan can take (blocked files, partial matches, clones,
nothing marked at all).
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core.deletion_plan import DeletionPlan  # noqa: E402
from qt.deletion_preview import DeletionPreview, _outcome  # noqa: E402


def make_plan(entries=None, **kwargs):
    """A plan with plausible defaults, overridable per test."""
    fields = {
        "groups": 1,
        "files": 1,
        "total_bytes": 1024,
        "partial": 0,
        "full_content": 1,
        "blocked": {},
        "blocked_bytes": 0,
        "cross_volume": 0,
        "cloneable": 0,
        "entries": entries if entries is not None else [],
    }
    fields.update(kwargs)
    return DeletionPlan(**fields)


def entry(ref="/ref.txt", **dupe):
    """One group: a reference and a single candidate."""
    dupe.setdefault("path", "/dupe.txt")
    dupe.setdefault("size", 1024)
    dupe.setdefault("would_delete", True)
    dupe.setdefault("match_confidence", "full")
    return {"reference": {"path": ref, "size": 1024}, "duplicates": [dupe]}


class TestOutcomeWording:
    """Each candidate's row says what will happen to that specific file."""

    def test_trash_is_distinguished_from_permanent_deletion(self):
        # The whole reason the preview takes direct_delete: "sent to trash" is recoverable
        # and "deleted permanently" is not, and the user is deciding between them.
        dupe = {"would_delete": True, "match_confidence": "full"}
        assert _outcome(dupe, direct_delete=False) == "sent to trash"
        assert _outcome(dupe, direct_delete=True) == "deleted permanently"

    def test_partial_matches_are_flagged_as_such(self):
        dupe = {"would_delete": True, "match_confidence": "partial"}
        assert "partial hash match only" in _outcome(dupe, direct_delete=False)

    def test_blocked_files_report_the_planner_s_own_reason(self):
        # Re-wording the reason here is exactly how the GUI and CLI would drift apart.
        dupe = {"would_delete": False, "blocked_reason": "file changed since last scan"}
        out = _outcome(dupe, direct_delete=False)
        assert "skipped" in out
        assert "file changed since last scan" in out

    def test_cloneable_files_do_not_claim_they_will_be_deleted(self):
        # A clone leaves both files in place. Telling the user it was "sent to trash" would
        # describe the opposite of what happens.
        dupe = {"would_delete": True, "match_confidence": "full", "cloneable": True}
        out = _outcome(dupe, direct_delete=True)
        assert "clone" in out
        assert "deleted" not in out


class TestDialog:
    def test_summary_reports_the_plan_s_figures(self, qapp):
        plan = make_plan(groups=3, files=7, total_bytes=5 * 1024 * 1024, partial=2, full_content=5)
        dialog = DeletionPreview(None, plan)
        summary = dialog.summaryLabel.text()
        assert "7 file(s) in 3 group(s)" in summary
        assert "5.00 MB" in summary
        assert "2 matched on a partial (sampled) hash only" in summary

    def test_every_candidate_gets_a_row_under_its_reference(self, qapp):
        plan = make_plan(entries=[entry(ref="/a/ref.txt", path="/a/dupe.txt")])
        dialog = DeletionPreview(None, plan)
        assert dialog.detailTree.topLevelItemCount() == 1
        group = dialog.detailTree.topLevelItem(0)
        assert group.text(0) == "/a/ref.txt"
        assert group.childCount() == 1
        assert group.child(0).text(0) == "/a/dupe.txt"
        assert group.child(0).text(2) == "sent to trash"

    def test_blocked_candidates_are_shown_not_hidden(self, qapp):
        # The failure the issue describes is learning about skips only from the problem
        # dialog afterwards. A preview that omitted them would reproduce it.
        plan = make_plan(
            files=0,
            full_content=0,
            blocked_bytes=1024,
            entries=[entry(would_delete=False, blocked_reason="file no longer exists")],
        )
        dialog = DeletionPreview(None, plan)
        row = dialog.detailTree.topLevelItem(0).child(0)
        assert "file no longer exists" in row.text(2)

    def test_an_empty_plan_still_opens(self, qapp):
        # Reachable: mark files, have every one of them change on disk, then preview.
        dialog = DeletionPreview(None, make_plan(groups=0, files=0, total_bytes=0, full_content=0))
        assert dialog.detailTree.topLevelItemCount() == 0
        assert "0 file(s)" in dialog.summaryLabel.text()

    def test_direct_delete_is_carried_into_the_rows(self, qapp):
        plan = make_plan(entries=[entry()])
        dialog = DeletionPreview(None, plan, direct_delete=True)
        assert "permanently delete" in dialog.summaryLabel.text()
        assert dialog.detailTree.topLevelItem(0).child(0).text(2) == "deleted permanently"


class TestWiredIntoTheDeletionOptionsDialog:
    """The preview is only useful if it is reachable from where deletions start."""

    def test_the_options_dialog_offers_a_preview(self, dgapp):
        assert dgapp.deletionOptions.previewButton.isEnabled()

    def test_clicking_preview_builds_and_shows_a_real_plan(self, dgapp, monkeypatch):
        # Guards the wiring, not the figures: that the button reaches core's planner and
        # hands what it gets to the dialog, with the options as currently set.
        shown = {}
        monkeypatch.setattr(dgapp.model, "deletion_preview", lambda: make_plan(files=2, groups=1))
        monkeypatch.setattr(
            "qt.deletion_options.DeletionPreview",
            lambda parent, plan, direct_delete=False: shown.update(plan=plan, direct=direct_delete)
            or type("_", (), {"exec": lambda self: None})(),
        )
        dgapp.deletionOptions.directCheckbox.setChecked(True)
        dgapp.deletionOptions.previewButton.click()
        assert shown["plan"].files == 2
        assert shown["direct"] is True, "the preview must describe the options the user can see"
        dgapp.deletionOptions.directCheckbox.setChecked(False)
