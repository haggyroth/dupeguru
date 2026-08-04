# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for core.gui.mark_dialog.MarkDialog, which previously had none.

This is the rule-based auto-marking engine: it picks which file in each group is kept and
marks every other one. Whatever it marks is what a subsequent delete removes, so it is the
component where a silent mistake costs the most.
"""

from hscommon.testutil import eq_

from core.gui.mark_dialog import MarkDialog
from core.tests.base import GetTestGroups, NamedObject, TestApp


def _app_with_groups():
    app = TestApp().app
    objects, matches, groups = GetTestGroups()
    app.results.groups = groups
    return app, objects, groups


def _marked(app):
    return {d.name for d in app.results.dupes if app.results.is_marked(d)}


# ---------------------------------------------------------------------------
# Rule list construction
# ---------------------------------------------------------------------------


def test_rule_names_are_populated():
    app, _, _ = _app_with_groups()
    dialog = MarkDialog(app)
    assert dialog.rule_names, "expected at least one rule from the prioritization categories"
    assert all(isinstance(name, str) and name for name in dialog.rule_names)


def test_rule_names_cover_every_category():
    """One entry per criterion across every category the app offers."""
    app, _, _ = _app_with_groups()
    expected = 0
    for cat_class in app._prioritization_categories():
        expected += len(cat_class(app.results).criteria_list())
    eq_(len(MarkDialog(app).rule_names), expected)


def test_rule_list_follows_the_categories_the_app_offers():
    """MarkDialog must build from whatever _prioritization_categories() returns.

    Not asserted through TestApp with app_mode switched: core/tests/base.py's DupeGuru
    overrides _prioritization_categories() to always return the standard set, so mode has
    no effect there. Using a stub tests MarkDialog's actual contract instead.
    """
    from core.pe import prioritize as pe_prioritize
    from core import prioritize as se_prioritize

    app, _, _ = _app_with_groups()

    class _StubApp:
        def __init__(self, categories):
            self.results = app.results
            self._categories = categories

        def _prioritization_categories(self):
            return self._categories

    standard = MarkDialog(_StubApp(se_prioritize.all_categories())).rule_names
    picture = MarkDialog(_StubApp(pe_prioritize.all_categories())).rule_names

    assert standard != picture
    assert any("Dimensions" in name for name in picture)
    assert not any("Dimensions" in name for name in standard)


def test_selected_index_defaults_to_zero():
    app, _, _ = _app_with_groups()
    eq_(MarkDialog(app).selected_index, 0)


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


def test_apply_marks_every_non_keeper():
    """Each group keeps exactly one file; everything else in it gets marked."""
    app, _, groups = _app_with_groups()
    dialog = MarkDialog(app)
    dialog.apply()
    expected = sum(len(g) - 1 for g in groups)
    eq_(app.results.mark_count, expected)


def test_apply_never_marks_the_group_reference():
    app, _, _ = _app_with_groups()
    MarkDialog(app).apply()
    for group in app.results.groups:
        assert not app.results.is_marked(group.ref), "the kept file must never be marked"


def test_apply_is_idempotent():
    app, _, _ = _app_with_groups()
    dialog = MarkDialog(app)
    dialog.apply()
    first = _marked(app)
    dialog.apply()
    eq_(_marked(app), first)


def test_apply_replaces_a_previous_marking_rather_than_adding_to_it():
    """apply() calls mark_none() before mark_all(), so stale marks must not survive."""
    app, _, groups = _app_with_groups()
    app.results.mark_all()
    dialog = MarkDialog(app)
    dialog.apply()
    eq_(app.results.mark_count, sum(len(g) - 1 for g in groups))


def test_apply_with_no_groups_is_harmless():
    app = TestApp().app
    app.results.groups = []
    MarkDialog(app).apply()
    eq_(app.results.mark_count, 0)


def test_apply_does_nothing_when_there_are_no_rules(monkeypatch):
    app, _, _ = _app_with_groups()
    dialog = MarkDialog(app)
    dialog._rules = []
    called = []
    monkeypatch.setattr(app, "mark_by_criterion", lambda key: called.append(key))
    dialog.apply()
    eq_(called, [])


def test_apply_uses_the_selected_rule(monkeypatch):
    app, _, _ = _app_with_groups()
    dialog = MarkDialog(app)
    assert len(dialog._rules) > 1
    dialog.selected_index = 1
    captured = []
    monkeypatch.setattr(app, "mark_by_criterion", lambda key: captured.append(key))
    dialog.apply()
    eq_(len(captured), 1)
    assert captured[0] == dialog._rules[1].sort_key


# ---------------------------------------------------------------------------
# Reference-folder protection
#
# MarkDialog's docstring promises files in a reference folder are never displaced and
# never marked. Group.prioritize sorts on (-is_ref, key_func) and Results._is_markable
# refuses is_ref dupes; these lock both halves of that in.
# ---------------------------------------------------------------------------


def test_reference_folder_file_is_never_marked():
    app = TestApp().app
    ref = NamedObject("ref_copy", size=100)
    other = NamedObject("ref_copy", size=100)
    ref.is_ref = True
    from core import engine

    matches = engine.getmatches([ref, other])
    app.results.groups = engine.get_groups(matches)

    MarkDialog(app).apply()

    assert not app.results.is_marked(ref), "a reference-folder file must never be marked"


def test_reference_folder_file_stays_the_keeper():
    """Even when the rule would rank it last, an is_ref file keeps the ref position."""
    app = TestApp().app
    # Rule "largest size" would prefer `other`; is_ref must win regardless.
    ref = NamedObject("candidate", size=1)
    other = NamedObject("candidate", size=9999)
    ref.is_ref = True
    from core import engine

    matches = engine.getmatches([ref, other])
    app.results.groups = engine.get_groups(matches)

    dialog = MarkDialog(app)
    for index, name in enumerate(dialog.rule_names):
        if "size" in name.lower():
            dialog.selected_index = index
            break
    dialog.apply()

    eq_(app.results.groups[0].ref, ref)
    assert not app.results.is_marked(ref)


# ---------------------------------------------------------------------------
# Marking actually changes with the rule
# ---------------------------------------------------------------------------


def test_opposite_size_rules_pick_opposite_keepers():
    """Rule selection must actually change the outcome, or the dialog is inert.

    Both files share a name so the engine groups them; they differ only in size, so
    "Size (Highest)" and "Size (Lowest)" must disagree about which one is kept.
    """
    from core import engine

    def keeper_for(rule_name):
        app = TestApp().app
        small = NamedObject("dupe", size=1, folder="a")
        large = NamedObject("dupe", size=9999, folder="b")
        app.results.groups = engine.get_groups(engine.getmatches([small, large]))
        dialog = MarkDialog(app)
        dialog.selected_index = dialog.rule_names.index(rule_name)
        dialog.apply()
        group = app.results.groups[0]
        return group.ref.size, {d.size for d in group.dupes if app.results.is_marked(d)}

    highest_ref, highest_marked = keeper_for("Size (Highest)")
    lowest_ref, lowest_marked = keeper_for("Size (Lowest)")

    eq_(highest_ref, 9999)
    eq_(highest_marked, {1})
    eq_(lowest_ref, 1)
    eq_(lowest_marked, {9999})
