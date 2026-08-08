# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Each mode's columns are declared twice, and the two lists must agree.

Core declares what a column *is* -- its name, title and default visibility. Qt declares how to
draw it -- width, editor, alignment. Adding a column means editing both, in six files, and
nothing connects them.

Getting it wrong is not a wrong pixel. ``qt.column.Columns`` looks every Qt column up by name
in the core list at construction time, so a name in one list and not the other raises inside
the results model -- which means the *results window fails to open after a scan completes*, with
an AttributeError about column specs and nothing pointing at the list that was missed. That is
a long way from the edit that caused it, which is the whole reason this file exists.

The failure is cheap to prevent and was hit for real while adding the confidence column
(#124), so it is pinned here rather than left to be rediscovered.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core.me.result_table import ResultTable as CoreMusic  # noqa: E402
from core.pe.result_table import ResultTable as CorePicture  # noqa: E402
from core.se.result_table import ResultTable as CoreStandard  # noqa: E402
from qt.me.results_model import ResultsModel as QtMusic  # noqa: E402
from qt.pe.results_model import ResultsModel as QtPicture  # noqa: E402
from qt.se.results_model import ResultsModel as QtStandard  # noqa: E402

MODES = {
    "standard": (CoreStandard, QtStandard),
    "picture": (CorePicture, QtPicture),
    "music": (CoreMusic, QtMusic),
}


def core_names(table):
    return [column.name for column in table.COLUMNS]


def qt_names(model):
    return [column.attrname for column in model.COLUMNS]


@pytest.mark.parametrize("mode", list(MODES))
class TestTheTwoListsAgree:
    def test_no_qt_column_is_missing_from_core(self, mode):
        # The direction that raises: Columns.__init__ resolves every Qt column by name against
        # the core list, so an unknown name breaks the results window rather than one cell.
        core, qt = MODES[mode]
        unknown = set(qt_names(qt)) - set(core_names(core))
        assert unknown == set(), f"{mode}: Qt declares columns core has never heard of: {unknown}"

    def test_no_core_column_is_missing_from_qt(self, mode):
        # Quieter, but still wrong: the column exists and is offered in the Columns menu, and
        # draws with fallback specs the mode never chose.
        core, qt = MODES[mode]
        undrawn = set(core_names(core)) - set(qt_names(qt))
        assert undrawn == set(), f"{mode}: core declares columns Qt never styles: {undrawn}"

    def test_the_order_matches(self, mode):
        # Qt's order is the left-to-right order of the table; core's is the order of the
        # Columns menu. Letting them drift means the menu stops matching the table.
        core, qt = MODES[mode]
        assert core_names(core) == qt_names(qt)

    def test_no_column_is_declared_twice(self, mode):
        core, qt = MODES[mode]
        for label, names in (("core", core_names(core)), ("qt", qt_names(qt))):
            duplicated = {name for name in names if names.count(name) > 1}
            assert duplicated == set(), f"{mode}: {label} declares {duplicated} more than once"
