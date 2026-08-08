# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The 'Mark by Rule' dialog.

Picking a rule here promotes one file per group to the reference position and **marks every
other file in that group for deletion**. The combobox row is used directly as an index into
the rule list, so the two have to stay in step: a mismatch means the user picks "keep newest"
and gets "keep smallest", marking a different set of files than they chose.

The Qt layer here is thin, which is precisely why it was uncovered -- and why the seam it
carries is worth a test.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtWidgets import QDialogButtonBox  # noqa: E402

from qt.mark_dialog import MarkDialog  # noqa: E402


@pytest.fixture
def dialog(dgapp):
    dialog = MarkDialog(None, dgapp)
    yield dialog
    dialog.close()


class TestRuleList:
    def test_every_rule_is_offered(self, dialog):
        assert dialog.ruleComboBox.count() == len(dialog.model.rule_names)
        assert dialog.ruleComboBox.count() > 0, "a dialog with no rules can do nothing"

    def test_the_rules_are_listed_in_the_model_s_order(self, dialog):
        # _ruleSelected assigns the combobox row straight to model.selected_index, and apply()
        # indexes its rule list by that number. Listing them in a different order would apply
        # a different rule than the one whose name the user read.
        shown = [dialog.ruleComboBox.itemText(row) for row in range(dialog.ruleComboBox.count())]
        assert shown == list(dialog.model.rule_names)

    def test_the_initial_selection_matches_the_model(self, dialog):
        assert dialog.ruleComboBox.currentIndex() == dialog.model.selected_index


class TestSelection:
    def test_choosing_a_rule_reaches_the_model(self, dialog):
        last = dialog.ruleComboBox.count() - 1
        dialog.ruleComboBox.setCurrentIndex(last)
        assert dialog.model.selected_index == last

    @pytest.mark.parametrize("row", [0, 1, 2])
    def test_each_row_selects_the_rule_with_that_name(self, dialog, row):
        if row >= dialog.ruleComboBox.count():
            pytest.skip("fewer rules than this row")
        dialog.ruleComboBox.setCurrentIndex(row)
        assert dialog.model.rule_names[dialog.model.selected_index] == dialog.ruleComboBox.itemText(row)


class TestButtons:
    def test_the_confirm_button_says_what_it_does(self, dialog):
        # "OK" gives no clue that pressing it marks files for deletion.
        button = dialog.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        assert button.text() == "Mark Others"

    def test_there_is_a_way_out(self, dialog):
        assert dialog.buttonBox.button(QDialogButtonBox.StandardButton.Cancel) is not None

    def test_rejecting_marks_nothing(self, dgapp, dialog):
        # The model only acts on apply(); closing must not trigger it.
        applied = []
        dialog.model.apply = lambda: applied.append(True)
        dialog.reject()
        assert applied == []
