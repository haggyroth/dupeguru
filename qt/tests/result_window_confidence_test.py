# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The confidence triage reaches the results window (issue #124).

The classification and the marking are tested against core. What is left is the part that only
exists in Qt, and it is the part with a precedent for going wrong quietly: ``--full-verify``
shipped wired to the CLI and unreachable from the GUI, because nothing checked that the menu
item existed. A bulk mark nobody can invoke is the same failure.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core.confidence import Confidence  # noqa: E402

MARK_ACTIONS = {
    "actionMarkCorroborated": Confidence.CORROBORATED,
    "actionMarkContentOnly": Confidence.CONTENT,
}


@pytest.fixture(autouse=True)
def never_block(dgapp, monkeypatch):
    """Collect messages instead of opening them.

    ``show_message`` is a modal ``QMessageBox``, so a regression that made these actions talk
    when they should stay quiet would *hang* the run rather than fail it -- and a test suite
    that hangs is worse than one that fails, because CI reports a timeout with nothing to read.
    Every test in this file triggers a real action, so this is patched for all of them and the
    two that care about the message read what it collected.
    """
    said = []
    monkeypatch.setattr(dgapp, "show_message", said.append)
    return said


@pytest.fixture
def result_window(dgapp):
    # The real path: a scan calls _recreate_result_table, which builds the table and then calls
    # back into the view to create the window. See qt/tests/app_test.py.
    dgapp.model._recreate_result_table()
    return dgapp.resultWindow


class TestTheMarkActions:
    @pytest.mark.parametrize("attr", list(MARK_ACTIONS))
    def test_the_action_exists(self, result_window, attr):
        assert getattr(result_window, attr).text()

    @pytest.mark.parametrize("attr", list(MARK_ACTIONS))
    def test_the_action_is_in_the_mark_menu(self, result_window, attr):
        # Existing but unreachable is the failure mode this file is here for.
        action = getattr(result_window, attr)
        assert action in result_window.menuMark.actions()

    @pytest.mark.parametrize("attr,tier", list(MARK_ACTIONS.items()))
    def test_triggering_the_action_marks_that_tier(self, result_window, attr, tier, monkeypatch):
        asked = []
        monkeypatch.setattr(result_window.app.model, "mark_confidence", lambda t: asked.append(t) or 1)
        getattr(result_window, attr).trigger()
        assert asked == [tier], "the action asked for the wrong tier"

    def test_marking_nothing_says_why(self, result_window, never_block, monkeypatch):
        # Clicking a menu item and seeing the results not change reads as a bug unless the
        # window says the tier was simply empty.
        monkeypatch.setattr(result_window.app.model, "mark_confidence", lambda tier: 0)
        result_window.actionMarkCorroborated.trigger()
        assert never_block and "corroborated" in never_block[0].lower()

    def test_marking_something_stays_quiet(self, result_window, never_block, monkeypatch):
        # A dialog on every successful use would be noise; the table and the stats label
        # already show what happened.
        monkeypatch.setattr(result_window.app.model, "mark_confidence", lambda tier: 3)
        result_window.actionMarkCorroborated.trigger()
        assert never_block == []

    def test_no_action_promises_safety(self, result_window):
        # Same reason the tiers avoid it: a menu item reading "Mark Safe Groups" would make a
        # promise about the user's files that dupeGuru is not in a position to make.
        for attr in MARK_ACTIONS:
            assert "safe" not in getattr(result_window, attr).text().lower()


class TestTheColumn:
    def test_the_column_is_offered(self, result_window):
        assert "confidence" in [column.attrname for column in result_window.resultsModel.COLUMNS]

    def test_the_column_starts_hidden(self, result_window):
        # Existing users' tables should not gain a column they never asked for; it is opt-in
        # from the Columns menu, like the other optional ones.
        column = result_window.app.model.result_table._columns.column_by_name("confidence")
        assert not column.visible
