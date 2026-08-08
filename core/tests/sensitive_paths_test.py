# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Warning before a scan of a system or application-support location (issue #134).

The issue is unusually clear about the failure mode, and it is not "misses a folder". It is
**warning fatigue**: a prompt that fires on folders people scan routinely gets dismissed
reflexively, and that habit carries over to the multi-drive and partial-hash prompts, which
guard real data loss. A false positive here therefore costs more than a false negative.

So the tests that matter most are the ones asserting that ordinary folders stay silent. The
platform lists themselves are only checked on the platform they belong to -- asserting that
``C:\\Windows`` is sensitive while running on macOS would be testing a string, not a behaviour.
"""

import tempfile
from pathlib import Path

import pytest

from core import sensitive_paths
from core.sensitive_paths import describe, reason_for, warnings_for
from hscommon.plat import ISLINUX, ISOSX, ISWINDOWS

on_macos = pytest.mark.skipif(not ISOSX, reason="macOS locations")
on_windows = pytest.mark.skipif(not ISWINDOWS, reason="Windows locations")
on_linux = pytest.mark.skipif(not ISLINUX, reason="Linux locations")


class TestOrdinaryFoldersStaySilent:
    """The tests the feature lives or dies by."""

    @pytest.mark.parametrize("folder", ["Documents", "Downloads", "Pictures", "Music", "Movies", "Desktop", "Projects"])
    def test_a_folder_in_the_home_directory_is_not_sensitive(self, folder):
        assert reason_for(Path.home() / folder) == ""

    def test_a_nested_user_folder_is_not_sensitive(self):
        assert reason_for(Path.home() / "Pictures" / "2026" / "holiday") == ""

    def test_the_home_directory_itself_is_not_sensitive(self):
        # Scanning your own home directory is one of the most ordinary things to do, even
        # though it *contains* a sensitive location on every platform.
        assert reason_for(Path.home()) == ""

    def test_a_temporary_directory_is_not_sensitive(self):
        # This one only bites on Windows, where the temp directory lives inside %LOCALAPPDATA%
        # and would otherwise warn on every run. It passes on macOS and Linux whether or not
        # the carve-out in reason_for exists, so removing that carve-out looks harmless from a
        # developer machine and fails only in the Windows CI job.
        assert reason_for(tempfile.mkdtemp()) == ""

    def test_pytest_s_own_temporary_directory_is_not_sensitive(self, tmp_path):
        assert reason_for(tmp_path) == ""

    def test_a_folder_that_does_not_exist_is_answered_rather_than_raising(self):
        # A saved directory list can outlive the folders in it, and the answer is wanted before
        # anything walks the tree.
        assert reason_for(Path.home() / "no-such-folder-b3f1") == ""


class TestTheWholeFilesystem:
    def test_the_root_is_flagged(self):
        assert reason_for(Path(Path.cwd().anchor)) == sensitive_paths.WHOLE_SYSTEM


@on_macos
class TestMacOS:
    @pytest.mark.parametrize("folder", ["/System", "/Library", "/Applications", "/usr", "/bin", "/sbin"])
    def test_the_system_locations_are_flagged(self, folder):
        assert reason_for(folder)

    def test_a_folder_inside_a_system_location_is_flagged(self):
        assert reason_for("/System/Library/Fonts") == sensitive_paths.OS_ITSELF

    def test_the_user_library_is_flagged(self):
        # Named in the issue as the case someone might legitimately want to clean, which is
        # exactly why this warns rather than refusing.
        assert reason_for(Path.home() / "Library" / "Application Support") == sensitive_paths.APP_SUPPORT

    def test_the_inside_of_an_application_bundle_is_flagged(self):
        # Wherever the bundle is -- dragging an app to the desktop does not make its innards
        # safe to deduplicate.
        assert reason_for(Path.home() / "Desktop" / "Thing.app" / "Contents") == sensitive_paths.INSIDE_A_BUNDLE

    def test_a_bundle_reports_the_more_specific_reason(self):
        # /Applications matches too; the bundle is the more useful thing to say.
        assert reason_for("/Applications/Safari.app/Contents") == sensitive_paths.INSIDE_A_BUNDLE

    def test_etc_is_flagged_through_its_real_path(self):
        # /etc is a symlink to /private/etc, so an unresolved comparison would miss it.
        assert reason_for("/etc") == sensitive_paths.OS_ITSELF

    def test_the_match_is_case_insensitive(self):
        # The filesystem is case-insensitive by default, so /system is the same directory.
        assert reason_for("/system")

    def test_an_external_volume_is_not_sensitive(self):
        assert reason_for("/Volumes/Backup/photos") == ""


@on_windows
class TestWindows:
    def test_the_windows_directory_is_flagged(self):
        assert reason_for("C:/Windows") == sensitive_paths.OS_ITSELF

    def test_program_files_is_flagged(self):
        assert reason_for("C:/Program Files") == sensitive_paths.INSTALLED_APPS

    def test_the_match_is_case_insensitive(self):
        assert reason_for("c:/windows/system32")

    def test_a_data_drive_is_not_sensitive(self):
        assert reason_for("D:/Photos/2026") == ""


@on_linux
class TestLinux:
    @pytest.mark.parametrize("folder", ["/usr", "/bin", "/etc", "/var", "/boot", "/opt"])
    def test_the_system_locations_are_flagged(self, folder):
        assert reason_for(folder)

    def test_a_folder_inside_one_is_flagged(self):
        assert reason_for("/usr/share/icons") == sensitive_paths.OS_ITSELF

    def test_a_mounted_data_disk_is_not_sensitive(self):
        assert reason_for("/mnt/photos") == ""

    def test_the_match_is_case_sensitive(self):
        # Unlike the other two platforms: /USR is a different directory from /usr here, and
        # folding case would invent a match that the filesystem does not have.
        assert reason_for("/USR") == ""


class TestCollectingWarnings:
    def test_only_the_risky_folders_come_back(self):
        found = warnings_for([Path.home() / "Documents", Path(Path.cwd().anchor)])
        assert len(found) == 1

    def test_the_reason_travels_with_its_folder(self):
        # A message listing three folders and one reason cannot say which folder it was about.
        root = Path(Path.cwd().anchor)
        [(path, reason)] = warnings_for([root, Path.home() / "Documents"])
        assert path == root
        assert reason == sensitive_paths.WHOLE_SYSTEM

    def test_the_order_given_is_the_order_returned(self):
        paths = [Path(Path.cwd().anchor), Path.home() / "Documents"]
        assert [path for path, _ in warnings_for(paths)] == [paths[0]]

    def test_nothing_risky_is_an_empty_list(self):
        assert warnings_for([Path.home() / "Documents", Path.home() / "Music"]) == []


class TestTheMessage:
    def test_nothing_to_warn_about_produces_no_message(self):
        assert describe([]) == ""

    def test_the_message_names_every_folder_and_its_reason(self):
        message = describe([(Path("/one"), "reason one"), (Path("/two"), "reason two")])
        for fragment in ("/one", "reason one", "/two", "reason two"):
            assert fragment in message

    def test_the_message_says_what_could_go_wrong(self):
        message = describe([(Path("/one"), "reason one")])
        assert "installed software" in message

    def test_the_message_does_not_forbid_anything(self):
        # It warns; it never refuses. Wording that told the user not to proceed would make the
        # prompt feel like a wall rather than a heads-up, and cleaning an application-support
        # directory is a real thing to want to do.
        message = describe([(Path("/one"), "reason one")]).lower()
        for word in ("cannot", "not allowed", "refuse", "forbidden", "must not"):
            assert word not in message

    def test_one_folder_and_several_are_worded_for_their_count(self):
        assert describe([(Path("/one"), "r")]).startswith("This folder is")
        assert describe([(Path("/one"), "r"), (Path("/two"), "r")]).startswith("These folders are")


class TestTheListItself:
    def test_this_platform_has_a_list(self):
        assert sensitive_paths.known_locations(), "no sensitive locations known for this platform"

    def test_more_specific_locations_are_consulted_first(self):
        lengths = [len(path.parts) for path, _ in sensitive_paths.known_locations()]
        assert lengths == sorted(lengths, reverse=True)

    def test_the_list_stays_small(self):
        # Not a style rule. Every entry is a chance to fire on a folder somebody scans
        # routinely, and a prompt that gets dismissed reflexively trains people to dismiss the
        # two prompts that guard real data loss.
        assert len(sensitive_paths.known_locations()) <= 12

    def test_no_entry_is_the_home_directory_or_the_root(self):
        # Either would warn on essentially every scan.
        never = {Path.home(), Path(Path.cwd().anchor)}
        for path, _ in sensitive_paths.known_locations():
            assert path not in never, f"{path} would fire on almost every scan"

    def test_every_entry_carries_a_reason(self):
        for path, reason in sensitive_paths.known_locations():
            assert reason, f"{path} has no reason attached"
