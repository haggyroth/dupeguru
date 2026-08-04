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
