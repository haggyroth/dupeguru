# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Smoke coverage for the Qt front end.

Until now nothing under ``qt/`` was imported by any test, and CI ran only ``core`` and
``hscommon``. That gap is not theoretical: ``--full-verify`` shipped in 4.6.0 wired to the
CLI but unreachable from the GUI, and a broken ``pyrcc5`` invocation produced an icon-less
build that still reported success. Neither could fail a test, because no test existed.

These are deliberately smoke tests. They assert that the real widgets construct, that
preferences reach the scan options, and that resources actually resolve -- the kinds of
breakage that are silent at runtime. They do not attempt to assert layout or behaviour.
"""

import pytest

# requirements.txt now installs a binding on every platform, so these are expected to run
# everywhere, Linux included. The guard stays because a Qt binding is still not strictly
# required to use the CLI: qtpy raises QtBindingsNotFoundError when it finds none, and that
# is an ImportError subclass, so importorskip turns a bindings-free checkout into skips
# rather than errors.
pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")


class TestApplicationConstruction:
    def test_app_constructs(self, dgapp):
        assert dgapp.model is not None
        assert dgapp.prefs is not None

    def test_directories_dialog_exists(self, dgapp):
        assert dgapp.directories_dialog is not None

    def test_progress_window_exists(self, dgapp):
        assert dgapp.progress_window is not None

    def test_about_box_constructs(self, dgapp):
        assert dgapp.about_box is not None

    def test_result_window_and_details_dialog_build(self, dgapp):
        # This is the real path: a scan calls _recreate_result_table, which builds the
        # table and then calls back into the view to create the results window. Calling
        # create_results_window directly as well would bind a second view to the same
        # model, which hscommon.gui.base rejects.
        dgapp.model._recreate_result_table()
        assert dgapp.resultWindow is not None
        assert dgapp.details_dialog is not None


class TestDialogsConstruct:
    """The dialogs `_setup` builds eagerly.

    Asserting the app's own instances rather than building fresh ones is deliberate: these
    are bound to core-side GUI models, and `hscommon.gui.base` asserts if a second view is
    bound to a model that already has one. Checking the real instances also exercises the
    construction path the app actually takes.
    """

    def test_problem_dialog(self, dgapp):
        assert dgapp.problemDialog is not None

    def test_deletion_options(self, dgapp):
        assert dgapp.deletionOptions is not None

    def test_ignore_list_dialog(self, dgapp):
        assert dgapp.ignoreListDialog is not None

    def test_exclude_list_dialog(self, dgapp):
        assert dgapp.excludeListDialog is not None

    def test_preferences_dialog_builds_and_loads(self, dgapp):
        """Not built by _setup, so this one is constructed here. Binds to prefs, not a model."""
        from qt.se.preferences_dialog import PreferencesDialog

        dialog = PreferencesDialog(None, dgapp)
        dialog.load()
        assert dialog.fullVerifyBox is not None


class TestPreferencesReachTheScanner:
    """The bridge that silently dropped --full-verify.

    ``DupeGuru.start_scanning`` copies options onto the scanner with a ``hasattr`` guard, so
    an option the scanner does not declare is discarded with no error anywhere. These assert
    the GUI half of that contract: that a preference actually lands in ``model.options``.
    """

    def test_full_verify_reaches_options(self, dgapp, restore_prefs):
        prefs = restore_prefs
        prefs.big_file_partial_hashes = True
        prefs.full_verify = True
        dgapp._update_options()
        assert dgapp.model.options["full_verify"] is True

    def test_full_verify_is_suppressed_without_partial_hashing(self, dgapp, restore_prefs):
        """Verification is a no-op with no partial matches, so it must not be requested."""
        prefs = restore_prefs
        prefs.big_file_partial_hashes = False
        prefs.full_verify = True
        dgapp._update_options()
        assert dgapp.model.options["full_verify"] is False

    def test_partial_hash_threshold_is_converted_to_bytes(self, dgapp, restore_prefs):
        prefs = restore_prefs
        prefs.big_file_partial_hashes = True
        prefs.big_file_size_threshold = 7  # MiB
        dgapp._update_options()
        assert dgapp.model.options["big_file_size_threshold"] == 7 * 1024 * 1024

    def test_every_option_the_gui_sets_reaches_some_scanner(self, dgapp):
        """The counterpart to the core-side test: catches a typo in either direction.

        Checked against every scanner class, not just the current mode's: the options dict
        is shared across modes, so picture-only options like match_scaled are legitimately
        absent from ScannerSE.
        """
        from core.me.scanner import ScannerME
        from core.pe.scanner import ScannerPE
        from core.se.scanner import ScannerSE

        dgapp._update_options()
        scanners = [ScannerSE(), ScannerME(), ScannerPE()]
        # Options legitimately consumed by the app rather than by any scanner.
        app_only = {
            "clean_empty_dirs",
            "copymove_dest_type",
            "escape_filter_regexp",
            "ignore_hardlink_matches",
            "rehash_ignore_mtime",
        }
        unknown = [k for k in dgapp.model.options if k not in app_only and not any(hasattr(s, k) for s in scanners)]
        assert not unknown, f"options that reach no scanner attribute and are silently dropped: {unknown}"


class TestResources:
    """A missing resource is a null pixmap, not an exception.

    That is how an empty ``qt/dg_rc.py`` once produced a GUI with no icons at all while the
    build reported success. These fail instead. The resources are embedded and committed
    now, so unlike before there is no build step for these to depend on.
    """

    def test_named_resources_load(self):
        from qt import resources

        for name in ("logo_se", "plus", "minus", "error"):
            assert not resources.pixmap(name).isNull(), f"resource {name} did not load"

    def test_every_declared_resource_loads(self):
        """Guards the whole manifest, not a hand-picked few."""
        from qt import resources

        assert resources.names(), "no resources are declared"
        missing = [n for n in resources.names() if resources.pixmap(n).isNull()]
        assert not missing, f"declared resources that do not load: {missing}"

    def test_unknown_resource_raises(self):
        """Better a KeyError than a silently blank icon."""
        from qt import resources

        with pytest.raises(KeyError):
            resources.data("no_such_resource")


class TestDialogAttributeNaming:
    """`_setup`'s floating-window branch is unreachable and untestable, so guard it here.

    `use_tabs` is hardcoded True, so the branch never runs; and it cannot be exercised by
    constructing a second DupeGuru, because two of them in one process abort inside Qt's
    widget teardown -- attempted, and it crashed the interpreter rather than failing.

    That combination is precisely how the defect this guards survived: the branch assigned
    `self.excludeDialog` while every reader -- `qt/app.py:excludeListTriggered` and
    `qt/tabbed_window.py` -- looks for `self.excludeListDialog`, so switching `use_tabs` off
    would have raised AttributeError. Its sibling `ignoreListDialog` is spelled consistently
    in both branches, which is the pattern this asserts.

    A source-level check is weak, but it is the only kind available here, and it is the
    difference between catching a reintroduction and not.
    """

    def test_old_exclude_dialog_spelling_is_gone(self):
        from pathlib import Path

        qt_dir = Path(__file__).resolve().parents[1]
        offenders = []
        for path in sorted(qt_dir.rglob("*.py")):
            if path.name in ("resources_data.py",) or "tests" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                # The old name, not as a prefix of the correct one.
                if "excludeDialog" in line:
                    offenders.append(f"{path.relative_to(qt_dir.parent)}:{i}")
        assert not offenders, (
            "'excludeDialog' is the old misspelling of 'excludeListDialog'; every reader "
            f"expects the latter: {offenders}"
        )

    def test_both_setup_branches_assign_the_same_dialog_attributes(self):
        """Whatever the tab branch binds, the floating branch must bind under the same name."""
        import inspect

        from qt.app import DupeGuru

        source = inspect.getsource(DupeGuru._setup)
        for name in ("ignoreListDialog", "excludeListDialog"):
            assigned = source.count(f"self.{name} = ")
            assert assigned == 2, (
                f"expected self.{name} to be assigned in both _setup branches, " f"found {assigned} assignment(s)"
            )


class TestFileListCachePreference:
    """The preference must reach Directories, and unticking must actually detach it.

    A preference that is stored and displayed but never connected is the failure mode this
    project keeps hitting -- a scanner knob that reaches the dialog and not the scan looks
    identical to a working one from the UI.
    """

    def test_enabling_attaches_a_cache(self, dgapp, restore_prefs):
        dgapp.prefs.cache_file_list = True
        dgapp._update_options()
        assert dgapp.model.directories.file_list_cache is not None

    def test_disabling_detaches_it(self, dgapp, restore_prefs):
        """Unticking has to stop the cache being used, not just stop refreshing it."""
        dgapp.prefs.cache_file_list = True
        dgapp._update_options()
        assert dgapp.model.directories.file_list_cache is not None

        dgapp.prefs.cache_file_list = False
        dgapp._update_options()
        assert dgapp.model.directories.file_list_cache is None

    def test_enabling_twice_reuses_the_same_cache(self, dgapp, restore_prefs):
        """_update_options runs before every scan; each one must not open another connection."""
        dgapp.prefs.cache_file_list = True
        dgapp._update_options()
        first = dgapp.model.directories.file_list_cache
        dgapp._update_options()
        assert dgapp.model.directories.file_list_cache is first

    def test_cache_lands_in_appdata(self, dgapp, restore_prefs):
        """Beside the other caches, not in the root of the application data folder (#94)."""
        from core.file_list_cache import default_cache_path

        dgapp.prefs.cache_file_list = True
        dgapp._update_options()
        expected = default_cache_path(dgapp.model.appdata)
        assert expected.startswith(dgapp.model.appdata)
        assert expected.endswith("file_list_cache.db")

    def test_default_is_off(self, dgapp, restore_prefs):
        """The cache trades a missed in-place edit for speed, so it must be opt-in."""
        dgapp.prefs.reset()
        assert dgapp.prefs.cache_file_list is False


class TestStyleSwitching:
    """qt/app.py:_set_style runs on every preferences change, on Windows."""

    def test_an_unavailable_style_falls_back_instead_of_unsetting_the_style(self, qapp):
        # QStyleFactory.create() returns None for a key this Qt build does not provide, and
        # QApplication.setStyle(None) is accepted silently -- no exception, no message, and an
        # application left with an undefined style. "windowsvista" is exactly that case: a Qt 5
        # name later versions do not always carry.
        from qtpy.QtWidgets import QApplication, QStyleFactory

        from qt.app import _apply_style

        _apply_style("a style that does not exist")

        assert QApplication.style() is not None
        assert QStyleFactory.create("Fusion") is not None, "the fallback must exist everywhere"

    def test_an_available_style_is_applied(self, qapp):
        from qtpy.QtWidgets import QApplication

        from qt.app import _apply_style

        _apply_style("Fusion")
        assert QApplication.style() is not None

    def test_switching_twice_leaves_a_live_style(self, qapp):
        # The crash signature was the *second* call: replacing a style destroys the first while
        # widgets still reference it.
        from qtpy.QtWidgets import QApplication

        from qt.app import _apply_style

        _apply_style("Fusion")
        _apply_style("Fusion")
        assert QApplication.style() is not None
