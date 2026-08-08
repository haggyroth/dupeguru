# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The Qt half of scan profiles (issue #133): which preferences travel, and the dialog.

A profile is only worth having if loading it actually changes what the next scan does. The
settings therefore go back through preferences and then through ``_update_options``, which is
what rebuilds the option dict the scanner reads -- so the test that matters most here is the
one asserting a saved setting reaches ``model.options`` after a round trip.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from core.app import AppMode  # noqa: E402
from core.directories import DirectoryState  # noqa: E402
from core.scan_profile import ScanProfile  # noqa: E402
from core.scanner import ScanType  # noqa: E402
from qt.scan_profile import (  # noqa: E402
    SCAN_PREFERENCES,
    SCAN_TYPE_KEY,
    apply_settings,
    capture_settings,
    describe,
)
from qt.scan_profile_dialog import ScanProfileDialog  # noqa: E402


class TestWhichPreferencesTravel:
    def test_every_named_preference_exists(self, dgapp):
        # A typo here fails silently: capture_settings skips names the prefs object does not
        # have, so the setting would simply never be saved.
        missing = [name for name in SCAN_PREFERENCES if not hasattr(dgapp.prefs, name)]
        assert missing == []

    def test_appearance_and_language_stay_out_of_profiles(self, dgapp):
        # Loading a saved scan should not restyle the application or change its language.
        for name in ("use_dark_style", "language", "tabs_default_pos", "reference_bold_font"):
            assert name not in SCAN_PREFERENCES

    def test_the_copy_destination_stays_out(self, dgapp):
        # Where copied files land is something you do to results, not part of finding them.
        assert "destination_type" not in SCAN_PREFERENCES

    def test_capture_includes_the_scan_type_for_the_mode(self, dgapp):
        dgapp.prefs.set_scan_type(AppMode.PICTURE, ScanType.EXIFTIMESTAMP)
        settings = capture_settings(dgapp.prefs, AppMode.PICTURE)
        assert settings[SCAN_TYPE_KEY] == ScanType.EXIFTIMESTAMP

    def test_the_scan_type_is_applied_to_the_profile_s_own_mode(self, dgapp, restore_prefs):
        # A picture profile carries a picture scan type. Writing it into whichever mode
        # happens to be showing would leave both modes wrong.
        prefs = restore_prefs
        before = prefs.get_scan_type(AppMode.STANDARD)
        apply_settings(prefs, {SCAN_TYPE_KEY: ScanType.EXIFTIMESTAMP}, AppMode.PICTURE)
        assert prefs.get_scan_type(AppMode.PICTURE) == ScanType.EXIFTIMESTAMP
        assert prefs.get_scan_type(AppMode.STANDARD) == before

    def test_unknown_settings_are_ignored_rather_than_fatal(self, dgapp, restore_prefs):
        # A profile written by a later version must still restore everything this one knows.
        apply_settings(restore_prefs, {"invented_later": True, "match_scaled": True}, AppMode.PICTURE)
        assert restore_prefs.match_scaled is True

    def test_a_captured_setting_survives_back_into_the_scan_options(self, dgapp, restore_prefs):
        # The end-to-end claim: change a preference, save, change it back, load, and the
        # option the scanner actually reads is the saved one.
        prefs = restore_prefs
        prefs.ignore_small_files = True
        prefs.small_file_threshold = 64
        settings = capture_settings(prefs, AppMode.STANDARD)

        prefs.ignore_small_files = False
        prefs.small_file_threshold = 0
        dgapp._update_options()
        assert dgapp.model.options["size_threshold"] == 0

        apply_settings(prefs, settings, AppMode.STANDARD)
        dgapp._update_options()
        assert dgapp.model.options["size_threshold"] == 64 * 1024


class TestDescribe:
    def _profile(self, **kwargs):
        kwargs.setdefault("name", "p")
        return ScanProfile(**kwargs)

    def test_names_the_mode(self):
        assert describe(self._profile(app_mode=AppMode.PICTURE)).startswith("Picture")
        assert describe(self._profile(app_mode=AppMode.MUSIC)).startswith("Music")

    def test_counts_folders_with_agreeing_grammar(self):
        assert "1 folder" in describe(self._profile(folders=["/a"]))
        assert "2 folders" in describe(self._profile(folders=["/a", "/b"]))

    def test_mentions_reference_folders(self):
        # Worth surfacing in the list: it is the difference between a profile that protects
        # your originals and one that does not.
        profile = self._profile(folders=["/a", "/b"], states={"/b": DirectoryState.REFERENCE})
        assert "1 reference" in describe(profile)

    def test_says_nothing_about_references_when_there_are_none(self):
        profile = self._profile(folders=["/a"], states={"/a": DirectoryState.EXCLUDED})
        assert "reference" not in describe(profile)


class TestDialog:
    @pytest.fixture
    def app_with_profiles(self, dgapp, tmp_path):
        (tmp_path / "here").mkdir()
        dgapp.model.scan_profiles.clear()
        dgapp.model.scan_profiles.set(ScanProfile("Present", AppMode.STANDARD, [str(tmp_path / "here")]))
        dgapp.model.scan_profiles.set(ScanProfile("Absent", AppMode.PICTURE, [str(tmp_path / "gone")]))
        yield dgapp
        dgapp.model.scan_profiles.clear()

    def test_every_saved_profile_is_listed(self, app_with_profiles):
        dialog = ScanProfileDialog(None, app_with_profiles)
        assert dialog.profileList.count() == 2

    def test_a_profile_with_missing_folders_says_so_before_it_is_loaded(self, app_with_profiles):
        # The failure this guards is loading a profile whose drive is unplugged, scanning
        # less than expected, and reading the thin result as a clean one.
        dialog = ScanProfileDialog(None, app_with_profiles)
        rows = {dialog.profileList.item(i).text(): i for i in range(dialog.profileList.count())}
        absent = next(text for text in rows if text.startswith("Absent"))
        present = next(text for text in rows if text.startswith("Present"))
        assert "missing" in absent
        assert "missing" not in present
        assert "gone" in dialog.profileList.item(rows[absent]).toolTip()

    def test_the_row_carries_the_name_not_the_label(self, app_with_profiles):
        # The label has a summary appended, so looking the profile up by display text fails.
        dialog = ScanProfileDialog(None, app_with_profiles)
        dialog.profileList.setCurrentRow(0)
        assert dialog.selectedName() in app_with_profiles.model.scan_profiles.names

    def test_loading_applies_the_selected_profile(self, app_with_profiles, monkeypatch):
        applied = []
        monkeypatch.setattr(app_with_profiles, "loadScanProfile", applied.append)
        dialog = ScanProfileDialog(None, app_with_profiles)
        dialog.profileList.setCurrentRow(0)
        name = dialog.selectedName()
        dialog.loadClicked()
        assert applied == [name]

    def test_an_empty_list_offers_nothing_to_press(self, dgapp):
        dgapp.model.scan_profiles.clear()
        dialog = ScanProfileDialog(None, dgapp)
        assert dialog.profileList.count() == 0
        assert not dialog.loadButton.isEnabled()
        assert not dialog.deleteButton.isEnabled()
        assert "No scan profiles" in dialog.headerLabel.text()


class TestMenuWiring:
    """The feature is only reachable if the File menu offers it."""

    def _file_menu_texts(self, dgapp):
        menu = dgapp.directories_dialog.menuFile
        return [action.text() for action in menu.actions()]

    def test_the_file_menu_offers_saving_and_browsing(self, dgapp):
        texts = self._file_menu_texts(dgapp)
        assert "Save Scan Profile..." in texts
        assert "Scan Profiles..." in texts

    def test_saving_with_no_folders_says_so_instead_of_saving_nothing(self, dgapp, monkeypatch):
        # An empty profile would load, scan nothing, and report no duplicates -- which reads
        # as a clean result rather than as a mistake.
        dgapp.model.directories.clear()
        dgapp.model.scan_profiles.clear()
        messages = []
        monkeypatch.setattr(dgapp, "show_message", messages.append)
        # Stubbed even though the guard should return before reaching it: without the stub, a
        # regression that drops the guard hangs the suite on a modal prompt instead of failing.
        monkeypatch.setattr("qt.directories_dialog.QInputDialog.getText", lambda *a, **kw: ("x", True))
        dgapp.directories_dialog.saveScanProfileTriggered()
        assert len(dgapp.model.scan_profiles) == 0
        assert messages and "at least one folder" in messages[0]

    def test_saving_captures_the_current_settings(self, dgapp, restore_prefs, tmp_path, monkeypatch):
        (tmp_path / "scan_me").mkdir()
        dgapp.model.scan_profiles.clear()
        dgapp.model.directories.clear()
        dgapp.model.directories.add_path(tmp_path / "scan_me")
        restore_prefs.match_scaled = True
        monkeypatch.setattr("qt.directories_dialog.QInputDialog.getText", lambda *a, **kw: ("Nightly", True))
        monkeypatch.setattr(dgapp.model, "save", lambda: None)

        dgapp.directories_dialog.saveScanProfileTriggered()

        profile = dgapp.model.scan_profiles.get("Nightly")
        assert profile is not None
        assert profile.folders == [str(tmp_path / "scan_me")]
        assert profile.settings["match_scaled"] is True
        dgapp.model.scan_profiles.clear()

    def test_a_cancelled_name_prompt_saves_nothing(self, dgapp, tmp_path, monkeypatch):
        (tmp_path / "scan_me2").mkdir()
        dgapp.model.scan_profiles.clear()
        dgapp.model.directories.clear()
        dgapp.model.directories.add_path(tmp_path / "scan_me2")
        monkeypatch.setattr("qt.directories_dialog.QInputDialog.getText", lambda *a, **kw: ("", False))
        dgapp.directories_dialog.saveScanProfileTriggered()
        assert len(dgapp.model.scan_profiles) == 0

    def test_loading_puts_the_mode_buttons_where_the_model_is(self, dgapp, restore_prefs, tmp_path):
        # Without the refresh, the radio buttons keep showing the old mode while the scan runs
        # in the new one.
        (tmp_path / "pics").mkdir()
        dgapp.model.scan_profiles.clear()
        dgapp.model.scan_profiles.set(
            ScanProfile("Pics", AppMode.PICTURE, [str(tmp_path / "pics")], {}, {SCAN_TYPE_KEY: ScanType.FUZZYBLOCK})
        )
        dgapp.directories_dialog.appModeRadioBox.selected_index = 0
        dgapp.model.app_mode = AppMode.STANDARD

        dgapp.loadScanProfile("Pics")

        assert dgapp.model.app_mode == AppMode.PICTURE
        assert dgapp.directories_dialog.appModeRadioBox.selected_index == 2
        dgapp.model.scan_profiles.clear()

    def test_loading_a_profile_with_missing_folders_tells_the_user(self, dgapp, restore_prefs, tmp_path, monkeypatch):
        (tmp_path / "real").mkdir()
        dgapp.model.scan_profiles.clear()
        dgapp.model.scan_profiles.set(
            ScanProfile("Half", AppMode.STANDARD, [str(tmp_path / "real"), str(tmp_path / "unplugged")])
        )
        messages = []
        monkeypatch.setattr(dgapp, "show_message", messages.append)

        dgapp.loadScanProfile("Half")

        assert messages, "a silently reduced scan is the failure this guards"
        assert "unplugged" in messages[0]
        assert [str(p) for p in dgapp.model.directories] == [str(tmp_path / "real")]
        dgapp.model.scan_profiles.clear()
