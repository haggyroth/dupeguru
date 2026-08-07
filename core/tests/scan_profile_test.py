# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Named scan configurations (issue #133).

Two things carry real risk and get most of the attention here.

Reference folders are the mechanism that stops dupeGuru proposing your originals for deletion.
A profile that restored the folders but lost their states would turn protected folders back
into ordinary ones, quietly, on a configuration the user saved precisely so they would not
have to set it up correctly again.

The second is a profile whose folders have gone. Scanning four folders where five were
expected produces fewer duplicates, and fewer duplicates looks exactly like a clean result.
"""

import pytest

import cli
from core.app import AppMode, DupeGuru
from core.directories import Directories, DirectoryState
from core.scan_profile import ProfileStore, ScanProfile, ScanProfileError


@pytest.fixture
def folders(tmp_path):
    """Three real folders to build profiles from."""
    for name in ("photos", "backup", "archive"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def directories(folders):
    directories = Directories()
    directories.add_path(folders / "photos")
    directories.add_path(folders / "backup")
    directories.set_state(folders / "backup", DirectoryState.REFERENCE)
    return directories


@pytest.fixture
def app():
    """A real app with the directory tree given a view, as the GUI would."""
    app = DupeGuru(view=cli._HeadlessView())
    app.directory_tree.view = type("V", (), {"refresh": lambda s: None, "refresh_states": lambda s: None})()
    return app


class TestCapture:
    def test_capture_takes_the_folders_in_order(self, directories, folders):
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        assert profile.folders == [str(folders / "photos"), str(folders / "backup")]

    def test_capture_keeps_reference_folders(self, directories, folders):
        # The whole point of a reference folder is that its files are never deleted. Losing
        # that on restore would silently unprotect them.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        assert profile.states == {str(folders / "backup"): DirectoryState.REFERENCE}

    def test_capture_keeps_the_mode(self, directories):
        assert ScanProfile.capture("p", directories, AppMode.PICTURE).app_mode == AppMode.PICTURE

    def test_settings_are_stored_verbatim(self, directories):
        settings = {"match_scaled": True, "filter_hardness": 90, "scanned_tags": {"artist"}}
        profile = ScanProfile.capture("p", directories, AppMode.PICTURE, settings)
        assert profile.settings == settings


class TestApply:
    def test_apply_replaces_rather_than_adds(self, directories, folders):
        # Loading a profile means "scan this", not "scan this as well as whatever was already
        # listed" -- which would scan folders the profile never mentioned.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        other = Directories()
        other.add_path(folders / "archive")
        profile.apply_folders(other)
        assert [str(p) for p in other] == profile.folders

    def test_apply_restores_reference_folders(self, directories, folders):
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        other = Directories()
        profile.apply_folders(other)
        assert other.get_state(folders / "backup") == DirectoryState.REFERENCE
        assert other.get_state(folders / "photos") == DirectoryState.NORMAL

    def test_missing_folders_are_reported_and_skipped(self, directories, folders):
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        (folders / "backup").rmdir()
        other = Directories()
        missing = profile.apply_folders(other)
        assert missing == [str(folders / "backup")]
        assert [str(p) for p in other] == [str(folders / "photos")], "the rest still loads"

    def test_missing_folders_can_be_checked_without_applying(self, directories, folders):
        # So a front end can warn before loading rather than after.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        assert profile.missing_folders() == []
        (folders / "backup").rmdir()
        assert profile.missing_folders() == [str(folders / "backup")]

    def test_a_state_for_a_vanished_folder_is_not_restored(self, directories, folders):
        # States are matched by prefix. A stale entry for a path that no longer exists would
        # capture a different folder later created at the same place -- and silently make it a
        # reference folder, or exclude it.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD)
        (folders / "backup").rmdir()
        other = Directories()
        profile.apply_folders(other)
        assert str(folders / "backup") not in [str(p) for p in other.states]


class TestStore:
    def test_saving_under_an_existing_name_replaces_it(self, directories):
        store = ProfileStore()
        store.set(ScanProfile("Photos", AppMode.STANDARD))
        store.set(ScanProfile("Photos", AppMode.PICTURE))
        assert len(store) == 1
        assert store.get("Photos").app_mode == AppMode.PICTURE

    def test_profiles_come_back_in_name_order(self):
        store = ProfileStore()
        for name in ("zeta", "Alpha", "middle"):
            store.set(ScanProfile(name))
        assert store.names == ["Alpha", "middle", "zeta"]

    def test_a_nameless_profile_is_refused(self):
        store = ProfileStore()
        with pytest.raises(ScanProfileError):
            store.set(ScanProfile("   "))

    def test_removing_an_unknown_name_is_harmless(self):
        store = ProfileStore()
        store.remove("never existed")


class TestPersistence:
    def _round_trip(self, profile, tmp_path):
        store = ProfileStore()
        store.set(profile)
        store.save_to_file(tmp_path / "profiles.xml")
        reloaded = ProfileStore()
        reloaded.load_from_file(tmp_path / "profiles.xml")
        return reloaded.get(profile.name)

    def test_a_profile_survives_the_round_trip_intact(self, directories, tmp_path):
        settings = {"match_scaled": True, "filter_hardness": 90, "custom": "text", "ratio": 1.5}
        profile = ScanProfile.capture("My Photos", directories, AppMode.PICTURE, settings)
        assert self._round_trip(profile, tmp_path) == profile

    @pytest.mark.parametrize(
        "value",
        [True, False, 0, 42, -1, 3.5, "", "some text", set(), {"artist", "title"}],
    )
    def test_each_settings_type_survives(self, directories, tmp_path, value):
        # bool is the one that bites: it is a subclass of int, and "True" does not parse as
        # one, so encoding order and decoding both have to get it right.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD, {"key": value})
        restored = self._round_trip(profile, tmp_path).settings["key"]
        assert restored == value
        assert type(restored) is type(value)

    def test_a_setting_that_cannot_be_stored_is_refused_at_save_time(self, directories, tmp_path):
        # Better to fail where the caller can see it than to write a file that reads back
        # missing a setting nobody notices is gone.
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD, {"key": {1: 2}})
        store = ProfileStore()
        store.set(profile)
        with pytest.raises(ScanProfileError):
            store.save_to_file(tmp_path / "profiles.xml")

    def test_saving_twice_produces_the_same_file(self, directories, tmp_path):
        profile = ScanProfile.capture("p", directories, AppMode.STANDARD, {"tags": {"b", "a", "c"}})
        store = ProfileStore()
        store.set(profile)
        store.save_to_file(tmp_path / "one.xml")
        store.save_to_file(tmp_path / "two.xml")
        assert (tmp_path / "one.xml").read_bytes() == (tmp_path / "two.xml").read_bytes()

    def test_a_corrupt_file_leaves_the_store_empty_rather_than_raising(self, tmp_path):
        # This is read during startup. A damaged profile file is not a reason to refuse to
        # launch, which is how last_directories.xml already behaves.
        bad = tmp_path / "profiles.xml"
        bad.write_text("this is not xml <<<")
        store = ProfileStore()
        store.load_from_file(bad)
        assert len(store) == 0

    def test_a_missing_file_is_harmless(self, tmp_path):
        store = ProfileStore()
        store.load_from_file(tmp_path / "nope.xml")
        assert len(store) == 0

    def test_one_unreadable_setting_does_not_cost_the_profile(self, tmp_path):
        # The folders and the rest of the configuration are still worth having.
        (tmp_path / "profiles.xml").write_text(
            '<scan_profiles><profile name="p" app_mode="0">'
            '<folder path="/tmp" />'
            '<setting key="good" type="int" value="7" />'
            '<setting key="bad" type="int" value="not a number" />'
            '<setting key="alien" type="complex" value="1+2j" />'
            "</profile></scan_profiles>"
        )
        store = ProfileStore()
        store.load_from_file(tmp_path / "profiles.xml")
        profile = store.get("p")
        assert profile.folders == ["/tmp"]
        assert profile.settings == {"good": 7}


class TestAppIntegration:
    def test_save_then_apply_restores_the_whole_configuration(self, app, folders):
        app.app_mode = AppMode.PICTURE
        app.directories.add_path(folders / "photos")
        app.directories.add_path(folders / "backup")
        app.directories.set_state(folders / "backup", DirectoryState.REFERENCE)
        app.save_scan_profile("My Photos", {"match_scaled": True})

        app.app_mode = AppMode.STANDARD
        app.directories.clear()
        app.directories.add_path(folders / "archive")

        missing = app.apply_scan_profile("My Photos")

        assert missing == []
        assert app.app_mode == AppMode.PICTURE
        assert [str(p) for p in app.directories] == [str(folders / "photos"), str(folders / "backup")]
        assert app.directories.get_state(folders / "backup") == DirectoryState.REFERENCE
        assert app.scan_profiles.get("My Photos").settings == {"match_scaled": True}

    def test_applying_an_unknown_profile_raises(self, app):
        with pytest.raises(ScanProfileError):
            app.apply_scan_profile("never saved")

    def test_deleting_a_profile_leaves_the_folders_alone(self, app, folders):
        app.directories.add_path(folders / "photos")
        app.save_scan_profile("p")
        app.delete_scan_profile("p")
        assert "p" not in app.scan_profiles
        assert [str(p) for p in app.directories] == [str(folders / "photos")]

    def test_profiles_are_notified_so_a_view_can_refresh(self, app, folders):
        seen = []
        app.directories.add_path(folders / "photos")

        class Listener:
            def dispatch(self, msg):
                seen.append(msg)

        app.add_listener(Listener())
        app.save_scan_profile("p")
        app.delete_scan_profile("p")
        assert seen.count("scan_profiles_changed") == 2
