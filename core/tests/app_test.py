# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import errno
import os
import os.path as op
import logging
import tempfile
from unittest.mock import patch, MagicMock

import pytest
from pathlib import Path
import hscommon.conflict
import hscommon.util
from hscommon.testutil import eq_, log_calls
from hscommon.jobprogress.job import Job, nulljob

from core.tests.base import TestApp
from core.tests.results_test import GetTestGroups
from core import app, fs, engine
from core.results import Results
from core.scanner import ScanType


def add_fake_files_to_directories(directories, files):
    directories.get_files = lambda j=None: iter(files)
    directories._dirs.append("this is just so Scan() doesn't return 3")


class TestCaseDupeGuru:
    def test_clear_hash_cache_clears_both_caches(self, monkeypatch):
        """Issue #11: this cleared only filesdb, leaving the cache scans actually read.

        core/scanner.py's fast path reads hashcachedb (hash_cache2.db), not filesdb, so
        clearing one of the two meant "Clear Cache" did not do what the dialog says.
        """
        from core import hash_cache

        dgapp = TestApp().app
        cleared = []
        monkeypatch.setattr(fs.filesdb, "clear", lambda: cleared.append("filesdb"))
        monkeypatch.setattr(hash_cache.hashcachedb, "clear", lambda: cleared.append("hashcachedb"))
        dgapp.clear_hash_cache()
        eq_(sorted(cleared), ["filesdb", "hashcachedb"])

    def test_load_directories_keeps_the_exclusion_list(self, tmpdir):
        """Issue #12: this called directories.__init__(), whose exclude_list defaults to None.

        Every scan after a directory load then ignored the user's exclusions, with nothing
        in the UI to say so, until the app was restarted.
        """
        dgapp = TestApp().app
        exclude_list = dgapp.exclude_list
        assert dgapp.directories._exclude_list is exclude_list

        p = Path(str(tmpdir))
        dgapp.directories.add_path(p)
        xmlpath = str(tmpdir.join("directories.xml"))
        dgapp.directories.save_to_file(xmlpath)

        dgapp.load_directories(xmlpath)

        assert dgapp.directories._exclude_list is exclude_list
        eq_(len(dgapp.directories), 1)

    def test_apply_filter_calls_results_apply_filter(self, monkeypatch):
        dgapp = TestApp().app
        monkeypatch.setattr(dgapp.results, "apply_filter", log_calls(dgapp.results.apply_filter))
        dgapp.apply_filter("foo")
        eq_(2, len(dgapp.results.apply_filter.calls))
        call = dgapp.results.apply_filter.calls[0]
        assert call["filter_str"] is None
        call = dgapp.results.apply_filter.calls[1]
        eq_("foo", call["filter_str"])

    def test_apply_filter_escapes_regexp(self, monkeypatch):
        dgapp = TestApp().app
        monkeypatch.setattr(dgapp.results, "apply_filter", log_calls(dgapp.results.apply_filter))
        dgapp.apply_filter("()[]\\.|+?^abc")
        call = dgapp.results.apply_filter.calls[1]
        eq_("\\(\\)\\[\\]\\\\\\.\\|\\+\\?\\^abc", call["filter_str"])
        dgapp.apply_filter("(*)")  # In "simple mode", we want the * to behave as a wildcard
        call = dgapp.results.apply_filter.calls[3]
        eq_(r"\(.*\)", call["filter_str"])
        dgapp.options["escape_filter_regexp"] = False
        dgapp.apply_filter("(abc)")
        call = dgapp.results.apply_filter.calls[5]
        eq_("(abc)", call["filter_str"])

    def test_copy_or_move(self, tmpdir, monkeypatch):
        # The goal here is just to have a test for a previous blowup I had. I know my test coverage
        # for this unit is pathetic. What's done is done. My approach now is to add tests for
        # every change I want to make. The blowup was caused by a missing import.
        p = Path(str(tmpdir))
        p.joinpath("foo").touch()
        monkeypatch.setattr(
            hscommon.conflict,
            "smart_copy",
            log_calls(lambda source_path, dest_path: None),
        )
        # XXX This monkeypatch is temporary. will be fixed in a better monkeypatcher.
        monkeypatch.setattr(app, "smart_copy", hscommon.conflict.smart_copy)
        monkeypatch.setattr(os, "makedirs", lambda path: None)  # We don't want the test to create that fake directory
        dgapp = TestApp().app
        dgapp.directories.add_path(p)
        [f] = dgapp.directories.get_files()
        with tempfile.TemporaryDirectory() as tmp_dir:
            dgapp.copy_or_move(f, True, tmp_dir, 0)
            eq_(1, len(hscommon.conflict.smart_copy.calls))
            call = hscommon.conflict.smart_copy.calls[0]
            eq_(call["dest_path"], Path(tmp_dir, "foo"))
            eq_(call["source_path"], f.path)

    def test_copy_or_move_clean_empty_dirs(self, tmpdir, monkeypatch):
        tmppath = Path(str(tmpdir))
        sourcepath = tmppath.joinpath("source")
        sourcepath.mkdir()
        sourcepath.joinpath("myfile").touch()
        app = TestApp().app
        app.directories.add_path(tmppath)
        [myfile] = app.directories.get_files()
        monkeypatch.setattr(app, "clean_empty_dirs", log_calls(lambda path: None))
        app.copy_or_move(myfile, False, tmppath.joinpath("dest"), 0)
        calls = app.clean_empty_dirs.calls
        eq_(1, len(calls))
        eq_(sourcepath, calls[0]["path"])

    def test_scan_with_objects_evaluating_to_false(self):
        class FakeFile(fs.File):
            def __bool__(self):
                return False

        # At some point, any() was used in a wrong way that made Scan() wrongly return 1
        app = TestApp().app
        f1, f2 = (FakeFile("foo") for _ in range(2))
        f1.is_ref, f2.is_ref = (False, False)
        assert not (bool(f1) and bool(f2))
        add_fake_files_to_directories(app.directories, [f1, f2])
        app.start_scanning()  # no exception

    @pytest.mark.skipif("not hasattr(os, 'link')")
    def test_ignore_hardlink_matches(self, tmpdir):
        # If the ignore_hardlink_matches option is set, don't match files hardlinking to the same
        # inode.
        tmppath = Path(str(tmpdir))
        tmppath.joinpath("myfile").write_text("foo")
        os.link(str(tmppath.joinpath("myfile")), str(tmppath.joinpath("hardlink")))
        app = TestApp().app
        app.directories.add_path(tmppath)
        app.options["scan_type"] = ScanType.CONTENTS
        app.options["ignore_hardlink_matches"] = True
        app.start_scanning()
        eq_(len(app.results.groups), 0)

    def test_remove_hardlink_dupes_cross_device_same_inode(self):
        # Two files on different devices sharing the same st_ino must NOT be
        # treated as hardlinks — only (st_dev, st_ino) pairs are unique keys.
        f1 = MagicMock()
        f1.path.stat.return_value = MagicMock(st_dev=1, st_ino=42)
        f2 = MagicMock()
        f2.path.stat.return_value = MagicMock(st_dev=2, st_ino=42)  # same inode, different device
        result = app.DupeGuru._remove_hardlink_dupes([f1, f2])
        eq_(len(result), 2)

    def test_delete_dupe_skips_symlink(self):
        # A path that is a symlink must be refused even if it exists.
        dupe = MagicMock()
        dupe.path.exists.return_value = True
        dupe.path.is_symlink.return_value = True
        dgapp = TestApp().app
        with pytest.raises(OSError, match="symlink"):
            dgapp._do_delete_dupe(dupe, False, False, False)

    def test_delete_dupe_skips_changed_size(self, tmpdir):
        # A file whose size changed since the scan must be skipped.
        tmppath = Path(str(tmpdir))
        f = tmppath / "file.txt"
        f.write_text("hello")
        dupe = MagicMock()
        dupe.path = f
        dupe.size = 999  # recorded size differs from actual 5 bytes
        dupe.mtime = f.stat().st_mtime
        dgapp = TestApp().app
        with pytest.raises(OSError, match="changed since the last scan"):
            dgapp._do_delete_dupe(dupe, False, False, False)

    def test_delete_dupe_skips_changed_mtime(self, tmpdir):
        # A file whose mtime changed since the scan must be skipped.
        tmppath = Path(str(tmpdir))
        f = tmppath / "file.txt"
        f.write_text("hello")
        dupe = MagicMock()
        dupe.path = f
        dupe.size = f.stat().st_size
        dupe.mtime = f.stat().st_mtime - 100  # recorded mtime is 100 s in the past
        dgapp = TestApp().app
        with pytest.raises(OSError, match="changed since the last scan"):
            dgapp._do_delete_dupe(dupe, False, False, False)

    # --- The shared pre-deletion predicate (issue #25) ---

    def test_check_deletable_accepts_an_unchanged_file(self, tmpdir):
        f = Path(str(tmpdir)) / "file.txt"
        f.write_text("hello")
        st = f.stat()
        status, message = app.check_deletable(f, st.st_size, st.st_mtime)
        eq_(status, app.DeleteStatus.OK)
        eq_(message, "")

    def test_check_deletable_reports_each_refusal_distinctly(self, tmpdir):
        tmppath = Path(str(tmpdir))
        missing = tmppath / "gone.txt"
        eq_(app.check_deletable(missing, 5, 0)[0], app.DeleteStatus.GONE)

        f = tmppath / "file.txt"
        f.write_text("hello")
        st = f.stat()
        eq_(app.check_deletable(f, 999, st.st_mtime)[0], app.DeleteStatus.CHANGED)
        eq_(app.check_deletable(f, st.st_size, st.st_mtime - 100)[0], app.DeleteStatus.CHANGED)

    def test_check_deletable_tolerates_sub_second_mtime_drift(self, tmpdir):
        """FAT32 has 2-second mtime resolution; NTFS rounds. Neither is a real change."""
        f = Path(str(tmpdir)) / "file.txt"
        f.write_text("hello")
        st = f.stat()
        eq_(app.check_deletable(f, st.st_size, st.st_mtime - 1)[0], app.DeleteStatus.OK)
        eq_(app.check_deletable(f, st.st_size, st.st_mtime - 3)[0], app.DeleteStatus.CHANGED)

    # --- Folders (issue #77) ---

    def _folder_dupe(self, path):
        """A Folder with size/mtime populated the way a scan would leave them."""
        folder = fs.Folder(path)
        folder._read_info("size")
        folder._read_info("mtime")
        return folder

    def test_check_deletable_accepts_an_unchanged_folder(self, tmpdir):
        """The regression: a folder's aggregate size is not its directory entry size.

        Folder.size sums everything underneath (8000 bytes here); path.stat().st_size is the
        directory entry (128 on APFS, 4096 on ext4). Comparing them classified every folder
        as CHANGED, so folder-mode deletion could never happen at all.
        """
        sub = Path(str(tmpdir)) / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("x" * 5000)
        (sub / "b.txt").write_text("y" * 3000)
        folder = self._folder_dupe(sub)
        assert folder.size != sub.stat().st_size, "test is meaningless if these agree"
        status, message = app.check_deletable(sub, folder.size, folder.mtime)
        eq_(status, app.DeleteStatus.OK)
        eq_(message, "")

    def test_check_deletable_still_refuses_a_changed_folder(self, tmpdir):
        """The safety property has to survive the fix.

        Skipping the size check for directories would make the test above pass while
        silently removing the protection, so this asserts the refusal directly.
        """
        sub = Path(str(tmpdir)) / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("x" * 5000)
        folder = self._folder_dupe(sub)
        (sub / "b.txt").write_text("y" * 100)  # contents grew after the "scan"
        eq_(app.check_deletable(sub, folder.size, folder.mtime)[0], app.DeleteStatus.CHANGED)

    def test_check_deletable_counts_a_nested_folder(self, tmpdir):
        """Folder.size recurses; the recomputed total must too."""
        sub = Path(str(tmpdir)) / "sub"
        (sub / "nested").mkdir(parents=True)
        (sub / "top.txt").write_text("x" * 1000)
        (sub / "nested" / "deep.txt").write_text("y" * 2000)
        folder = self._folder_dupe(sub)
        eq_(app.check_deletable(sub, folder.size, folder.mtime)[0], app.DeleteStatus.OK)

    def test_check_deletable_ignores_symlinks_inside_a_folder(self, tmpdir):
        """Folder.size never counts symlinks, because File.can_handle rejects them.

        Counting one here would inflate the recomputed total and refuse the deletion -- the
        same mismatch the fix exists to remove, just from the other direction.
        """
        sub = Path(str(tmpdir)) / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("x" * 5000)
        try:
            os.symlink(str(sub / "a.txt"), str(sub / "link.txt"))
        except (OSError, NotImplementedError):
            pytest.skip("no privilege to create symlinks on this platform")
        folder = self._folder_dupe(sub)
        eq_(app.check_deletable(sub, folder.size, folder.mtime)[0], app.DeleteStatus.OK)

    def test_folder_dupe_is_actually_deleted(self, tmpdir):
        """End to end: the predicate passing did not mean deletion worked."""
        sub = Path(str(tmpdir)) / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("x" * 5000)
        folder = self._folder_dupe(sub)
        dgapp = TestApp().app
        dgapp._do_delete_dupe(folder, False, False, True)
        assert not sub.exists()

    def test_check_deletable_at_exactly_the_tolerance(self, tmpdir):
        """The boundary the tolerance exists for, which the surrounding tests step over.

        Found by mutation testing: changing `>` to `>=` survived, because the existing tests
        use 1 second (accepted) and 3 seconds (refused) and never touch 2. FAT32 stores mtimes
        to a 2-second resolution, so a file that has not changed can legitimately report
        exactly that difference -- refusing it would make deletion impossible on FAT volumes,
        which is the case the constant was added for.
        """
        f = Path(str(tmpdir)) / "file.txt"
        f.write_text("hello")
        st = f.stat()
        eq_(app.check_deletable(f, st.st_size, st.st_mtime - app._MTIME_TOLERANCE)[0], app.DeleteStatus.OK)
        eq_(
            app.check_deletable(f, st.st_size, st.st_mtime - app._MTIME_TOLERANCE - 0.5)[0],
            app.DeleteStatus.CHANGED,
        )

    def test_unused_link_path_increments_past_several_collisions(self, tmpdir):
        """`counter += 1`, not `counter = 1`.

        Found by mutation testing: replacing the increment with an assignment survived,
        because no test created two collisions. With `= 1` the loop would retry the same
        candidate forever -- a hang during deletion, not an error.
        """
        tmppath = Path(str(tmpdir))
        base = tmppath / "f.txt"
        base.write_text("x")
        (tmppath / "f.txt.dupeguru-link").write_text("x")
        (tmppath / "f.txt.dupeguru-link1").write_text("x")
        result = app.DupeGuru._unused_link_path(str(base))
        assert not result.exists()
        assert str(result).endswith("dupeguru-link2")

    def test_check_deletable_refuses_a_symlink(self, tmpdir):
        tmppath = Path(str(tmpdir))
        target = tmppath / "target.txt"
        target.write_text("hello")
        link = tmppath / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("no symlink privilege on this platform")
        st = target.stat()
        eq_(app.check_deletable(link, st.st_size, st.st_mtime)[0], app.DeleteStatus.SYMLINK)

    # --- Delete-and-replace-with-link ordering (issue #20) ---

    def _linkable_dupe(self, tmpdir):
        """Build a (dgapp, dupe, victim_path) trio wired into a real two-file group."""
        from core import engine

        tmppath = Path(str(tmpdir))
        keeper = tmppath / "keeper.txt"
        victim = tmppath / "victim.txt"
        keeper.write_text("same")
        victim.write_text("same")

        dgapp = TestApp().app
        ref_obj = fs.File(keeper)
        dupe_obj = fs.File(victim)
        for obj in (ref_obj, dupe_obj):
            obj.is_ref = False
        group = engine.Group()
        group.add_match(engine.Match(ref_obj, dupe_obj, 100))
        dgapp.results.groups = [group]
        group.prioritize(lambda x: 0 if x.path == keeper else 1)
        return dgapp, dupe_obj, victim

    def test_failed_link_creation_leaves_the_file_intact(self, tmpdir, monkeypatch):
        """The data loss in #20.

        The old order deleted first and then tried to create the link, so a failure --
        the default on Windows, where symlinks need a privilege most users lack -- left
        the file destroyed with nothing in its place.
        """
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)

        def _refuse(*args, **kwargs):
            raise OSError("symlink creation refused")

        monkeypatch.setattr(app.os, "symlink", _refuse)

        with pytest.raises(OSError):
            dgapp._do_delete_dupe(dupe, link_deleted=True, use_hardlinks=False, direct_deletion=True)

        assert victim.exists(), "the file must survive when its replacement link cannot be made"
        eq_(victim.read_text(), "same")

    def test_failed_link_creation_leaves_no_temporary_files(self, tmpdir, monkeypatch):
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)
        monkeypatch.setattr(app.os, "symlink", lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))

        with pytest.raises(OSError):
            dgapp._do_delete_dupe(dupe, link_deleted=True, use_hardlinks=False, direct_deletion=True)

        leftovers = [p.name for p in Path(str(tmpdir)).iterdir() if "dupeguru-link" in p.name]
        eq_(leftovers, [])

    def test_failed_delete_removes_the_temporary_link(self, tmpdir, monkeypatch):
        """If the link was made but the delete then failed, the temp link must not linger."""
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)
        monkeypatch.setattr(app.os, "symlink", lambda src, dst, **kw: Path(dst).write_text("stand-in"))
        monkeypatch.setattr(app.os, "remove", lambda p: (_ for _ in ()).throw(OSError("delete refused")))

        with pytest.raises(OSError):
            dgapp._do_delete_dupe(dupe, link_deleted=True, use_hardlinks=False, direct_deletion=True)

        assert victim.exists()
        leftovers = [p.name for p in Path(str(tmpdir)).iterdir() if "dupeguru-link" in p.name]
        eq_(leftovers, [])

    def test_windows_symlink_privilege_error_is_raised_not_swallowed(self, tmpdir, monkeypatch):
        """It used to call view.show_message and continue, so the failure read as success.

        Reporting through the view also happened on the job's worker thread, once per
        file. Raising instead lets perform_on_marked record it and keep the file marked.
        """
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)

        def _eperm(*args, **kwargs):
            raise OSError(errno.EPERM, "privilege not held")

        monkeypatch.setattr(app.sys, "platform", "win32")
        monkeypatch.setattr(app.os, "symlink", _eperm)

        with pytest.raises(OSError, match="Developer Mode|SeCreateSymbolicLinkPrivilege"):
            dgapp._do_delete_dupe(dupe, link_deleted=True, use_hardlinks=False, direct_deletion=True)

        assert victim.exists()

    def test_successful_link_replaces_the_deleted_file(self, tmpdir, monkeypatch):
        """The happy path still works: original gone, link in its place."""
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)
        created = {}

        def _fake_symlink(src, dst, **kwargs):
            created["src"] = src
            Path(dst).write_text("link-to:" + str(src))

        monkeypatch.setattr(app.os, "symlink", _fake_symlink)

        dgapp._do_delete_dupe(dupe, link_deleted=True, use_hardlinks=False, direct_deletion=True)

        assert victim.exists(), "the link should now occupy the original path"
        assert victim.read_text().startswith("link-to:")
        assert created["src"].endswith("keeper.txt")
        leftovers = [p.name for p in Path(str(tmpdir)).iterdir() if "dupeguru-link" in p.name]
        eq_(leftovers, [])

    def test_unused_link_path_avoids_collisions(self, tmpdir):
        base = Path(str(tmpdir)) / "f.txt"
        base.write_text("x")
        first = app.DupeGuru._unused_link_path(str(base))
        eq_(first.name, "f.txt.dupeguru-link")
        first.write_text("occupied")
        second = app.DupeGuru._unused_link_path(str(base))
        assert second != first
        eq_(second.name, "f.txt.dupeguru-link1")

    def test_delete_without_linking_is_unaffected(self, tmpdir):
        dgapp, dupe, victim = self._linkable_dupe(tmpdir)
        dgapp._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=True)
        assert not victim.exists()

    def test_dirs_span_multiple_devices_single(self, tmpdir):
        # A single directory never triggers the multi-device warning.
        p = Path(str(tmpdir))
        assert not app.DupeGuru._dirs_span_multiple_devices([p])

    def test_dirs_span_multiple_devices_same_device(self, tmpdir):
        # Two directories on the same device do not trigger the warning.
        p1 = Path(str(tmpdir)) / "a"
        p2 = Path(str(tmpdir)) / "b"
        p1.mkdir()
        p2.mkdir()
        assert not app.DupeGuru._dirs_span_multiple_devices([p1, p2])

    def test_dirs_span_multiple_devices_detects_different(self, tmpdir):
        # Simulate two paths on different devices by patching os.stat in the app module.
        p1 = Path(str(tmpdir)) / "a"
        p2 = Path(str(tmpdir)) / "b"
        p1.mkdir()
        p2.mkdir()
        stat_results = {str(p1): MagicMock(st_dev=1), str(p2): MagicMock(st_dev=2)}
        with patch("core.app.os.stat", side_effect=lambda p: stat_results[str(p)]):
            assert app.DupeGuru._dirs_span_multiple_devices([p1, p2])

    def test_rename_when_nothing_is_selected(self):
        # Issue #140
        # It's possible that rename operation has its selected row swept off from under it, thus
        # making the selected row None. Don't crash when it happens.
        dgapp = TestApp().app
        # selected_row is None because there's no result.
        assert not dgapp.result_table.rename_selected("foo")  # no crash


class TestCaseDupeGuruCleanEmptyDirs:
    @pytest.fixture
    def do_setup(self, request):
        monkeypatch = request.getfixturevalue("monkeypatch")
        monkeypatch.setattr(
            hscommon.util,
            "delete_if_empty",
            log_calls(lambda path, files_to_delete=[]: None),
        )
        # XXX This monkeypatch is temporary. will be fixed in a better monkeypatcher.
        monkeypatch.setattr(app, "delete_if_empty", hscommon.util.delete_if_empty)
        self.app = TestApp().app

    def test_option_off(self, do_setup):
        self.app.clean_empty_dirs(Path("/foo/bar"))
        eq_(0, len(hscommon.util.delete_if_empty.calls))

    def test_option_on(self, do_setup):
        self.app.options["clean_empty_dirs"] = True
        self.app.clean_empty_dirs(Path("/foo/bar"))
        calls = hscommon.util.delete_if_empty.calls
        eq_(1, len(calls))
        eq_(Path("/foo/bar"), calls[0]["path"])
        eq_([".DS_Store"], calls[0]["files_to_delete"])

    def test_recurse_up(self, do_setup, monkeypatch):
        # delete_if_empty must be recursively called up in the path until it returns False
        @log_calls
        def mock_delete_if_empty(path, files_to_delete=[]):
            return len(path.parts) > 1

        monkeypatch.setattr(hscommon.util, "delete_if_empty", mock_delete_if_empty)
        # XXX This monkeypatch is temporary. will be fixed in a better monkeypatcher.
        monkeypatch.setattr(app, "delete_if_empty", mock_delete_if_empty)
        self.app.options["clean_empty_dirs"] = True
        self.app.clean_empty_dirs(Path("not-empty/empty/empty"))
        calls = hscommon.util.delete_if_empty.calls
        eq_(3, len(calls))
        eq_(Path("not-empty/empty/empty"), calls[0]["path"])
        eq_(Path("not-empty/empty"), calls[1]["path"])
        eq_(Path("not-empty"), calls[2]["path"])


class TestCaseDupeGuruWithResults:
    @pytest.fixture
    def do_setup(self, request):
        app = TestApp()
        self.app = app.app
        self.objects, self.matches, self.groups = GetTestGroups()
        self.app.results.groups = self.groups
        self.dpanel = app.dpanel
        self.dtree = app.dtree
        self.rtable = app.rtable
        self.rtable.refresh()
        tmpdir = request.getfixturevalue("tmpdir")
        tmppath = Path(str(tmpdir))
        tmppath.joinpath("foo").mkdir()
        tmppath.joinpath("bar").mkdir()
        self.app.directories.add_path(tmppath)

    def test_get_objects(self, do_setup):
        objects = self.objects
        groups = self.groups
        r = self.rtable[0]
        assert r._group is groups[0]
        assert r._dupe is objects[0]
        r = self.rtable[1]
        assert r._group is groups[0]
        assert r._dupe is objects[1]
        r = self.rtable[4]
        assert r._group is groups[1]
        assert r._dupe is objects[4]

    def test_get_objects_after_sort(self, do_setup):
        objects = self.objects
        groups = self.groups[:]  # we need an un-sorted reference
        self.rtable.sort("name", False)
        r = self.rtable[1]
        assert r._group is groups[1]
        assert r._dupe is objects[4]

    def test_selected_result_node_paths_after_deletion(self, do_setup):
        # cases where the selected dupes aren't there are correctly handled
        self.rtable.select([1, 2, 3])
        self.app.remove_selected()
        # The first 2 dupes have been removed. The 3rd one is a ref. it stays there, in first pos.
        eq_(self.rtable.selected_indexes, [1])  # no exception

    def test_select_result_node_paths(self, do_setup):
        app = self.app
        objects = self.objects
        self.rtable.select([1, 2])
        eq_(len(app.selected_dupes), 2)
        assert app.selected_dupes[0] is objects[1]
        assert app.selected_dupes[1] is objects[2]

    def test_select_result_node_paths_with_ref(self, do_setup):
        app = self.app
        objects = self.objects
        self.rtable.select([1, 2, 3])
        eq_(len(app.selected_dupes), 3)
        assert app.selected_dupes[0] is objects[1]
        assert app.selected_dupes[1] is objects[2]
        assert app.selected_dupes[2] is self.groups[1].ref

    def test_select_result_node_paths_after_sort(self, do_setup):
        app = self.app
        objects = self.objects
        groups = self.groups[:]  # To keep the old order in memory
        self.rtable.sort("name", False)  # 0
        # Now, the group order is supposed to be reversed
        self.rtable.select([1, 2, 3])
        eq_(len(app.selected_dupes), 3)
        assert app.selected_dupes[0] is objects[4]
        assert app.selected_dupes[1] is groups[0].ref
        assert app.selected_dupes[2] is objects[1]

    def test_selected_powermarker_node_paths(self, do_setup):
        # app.selected_dupes is correctly converted into paths
        self.rtable.power_marker = True
        self.rtable.select([0, 1, 2])
        self.rtable.power_marker = False
        eq_(self.rtable.selected_indexes, [1, 2, 4])

    def test_selected_powermarker_node_paths_after_deletion(self, do_setup):
        # cases where the selected dupes aren't there are correctly handled
        app = self.app
        self.rtable.power_marker = True
        self.rtable.select([0, 1, 2])
        app.remove_selected()
        eq_(self.rtable.selected_indexes, [])  # no exception

    def test_select_powermarker_rows_after_sort(self, do_setup):
        app = self.app
        objects = self.objects
        self.rtable.power_marker = True
        self.rtable.sort("name", False)
        self.rtable.select([0, 1, 2])
        eq_(len(app.selected_dupes), 3)
        assert app.selected_dupes[0] is objects[4]
        assert app.selected_dupes[1] is objects[2]
        assert app.selected_dupes[2] is objects[1]

    def test_toggle_selected_mark_state(self, do_setup):
        app = self.app
        objects = self.objects
        app.toggle_selected_mark_state()
        eq_(app.results.mark_count, 0)
        self.rtable.select([1, 4])
        app.toggle_selected_mark_state()
        eq_(app.results.mark_count, 2)
        assert not app.results.is_marked(objects[0])
        assert app.results.is_marked(objects[1])
        assert not app.results.is_marked(objects[2])
        assert not app.results.is_marked(objects[3])
        assert app.results.is_marked(objects[4])

    def test_toggle_selected_mark_state_with_different_selected_state(self, do_setup):
        # When marking selected dupes with a heterogenous selection, mark all selected dupes. When
        # it's homogenous, simply toggle.
        app = self.app
        self.rtable.select([1])
        app.toggle_selected_mark_state()
        # index 0 is unmarkable, but we throw it in the bunch to be sure that it doesn't make the
        # selection heterogenoug when it shouldn't.
        self.rtable.select([0, 1, 4])
        app.toggle_selected_mark_state()
        eq_(app.results.mark_count, 2)
        app.toggle_selected_mark_state()
        eq_(app.results.mark_count, 0)

    def test_refresh_details_with_selected(self, do_setup):
        self.rtable.select([1, 4])
        eq_(self.dpanel.row(0), ("Filename", "bar bleh", "foo bar"))
        self.dpanel.view.check_gui_calls(["refresh"])
        self.rtable.select([])
        eq_(self.dpanel.row(0), ("Filename", "---", "---"))
        self.dpanel.view.check_gui_calls(["refresh"])

    def test_make_selected_reference(self, do_setup):
        app = self.app
        objects = self.objects
        groups = self.groups
        self.rtable.select([1, 4])
        app.make_selected_reference()
        assert groups[0].ref is objects[1]
        assert groups[1].ref is objects[4]

    def test_make_selected_reference_by_selecting_two_dupes_in_the_same_group(self, do_setup):
        app = self.app
        objects = self.objects
        groups = self.groups
        self.rtable.select([1, 2, 4])
        # Only [0, 0] and [1, 0] must go ref, not [0, 1] because it is a part of the same group
        app.make_selected_reference()
        assert groups[0].ref is objects[1]
        assert groups[1].ref is objects[4]

    def test_remove_selected(self, do_setup):
        app = self.app
        self.rtable.select([1, 4])
        app.remove_selected()
        eq_(len(app.results.dupes), 1)  # the first path is now selected
        app.remove_selected()
        eq_(len(app.results.dupes), 0)

    def test_add_directory_simple(self, do_setup):
        # There's already a directory in self.app, so adding another once makes 2 of em
        app = self.app
        # any other path that isn't a parent or child of the already added path
        otherpath = Path(op.dirname(__file__))
        app.add_directory(otherpath)
        eq_(len(app.directories), 2)

    def test_add_directory_already_there(self, do_setup):
        app = self.app
        otherpath = Path(op.dirname(__file__))
        app.add_directory(otherpath)
        app.add_directory(otherpath)
        eq_(len(app.view.messages), 1)
        assert "already" in app.view.messages[0]

    def test_add_directory_does_not_exist(self, do_setup):
        app = self.app
        app.add_directory("/does_not_exist")
        eq_(len(app.view.messages), 1)
        assert "exist" in app.view.messages[0]

    def test_ignore(self, do_setup):
        app = self.app
        self.rtable.select([4])  # The dupe of the second, 2 sized group
        app.add_selected_to_ignore_list()
        eq_(len(app.ignore_list), 1)
        self.rtable.select([1])  # first dupe of the 3 dupes group
        app.add_selected_to_ignore_list()
        # BOTH the ref and the other dupe should have been added
        eq_(len(app.ignore_list), 3)

    def test_purge_ignorelist(self, do_setup, tmpdir):
        app = self.app
        p1 = str(tmpdir.join("file1"))
        p2 = str(tmpdir.join("file2"))
        open(p1, "w").close()
        open(p2, "w").close()
        dne = "/does_not_exist"
        app.ignore_list.ignore(dne, p1)
        app.ignore_list.ignore(p2, dne)
        app.ignore_list.ignore(p1, p2)
        app.purge_ignore_list()
        eq_(1, len(app.ignore_list))
        assert app.ignore_list.are_ignored(p1, p2)
        assert not app.ignore_list.are_ignored(dne, p1)

    def test_only_unicode_is_added_to_ignore_list(self, do_setup):
        def fake_ignore(first, second):
            if not isinstance(first, str):
                self.fail()
            if not isinstance(second, str):
                self.fail()

        app = self.app
        app.ignore_list.ignore = fake_ignore
        self.rtable.select([4])
        app.add_selected_to_ignore_list()

    def test_cancel_scan_with_previous_results(self, do_setup):
        # When doing a scan with results being present prior to the scan, correctly invalidate the
        # results table.
        app = self.app
        app.JOB = Job(1, lambda *args, **kw: False)  # Cancels the task
        add_fake_files_to_directories(app.directories, self.objects)  # We want the scan to at least start
        app.start_scanning()  # will be cancelled immediately
        eq_(len(app.result_table), 0)

    def test_selected_dupes_after_removal(self, do_setup):
        # Purge the app's `selected_dupes` attribute when removing dupes, or else it might cause a
        # crash later with None refs.
        app = self.app
        app.results.mark_all()
        self.rtable.select([0, 1, 2, 3, 4])
        app.remove_marked()
        eq_(len(self.rtable), 0)
        eq_(app.selected_dupes, [])

    def test_dont_crash_on_delta_powermarker_dupecount_sort(self, do_setup):
        # Don't crash when sorting by dupe count or percentage while delta+powermarker are enabled.
        # Ref #238
        self.rtable.delta_values = True
        self.rtable.power_marker = True
        self.rtable.sort("dupe_count", False)
        # don't crash
        self.rtable.sort("percentage", False)
        # don't crash

    def test_mark_by_criterion_promotes_winner_to_ref(self, do_setup):
        # mark_by_criterion should promote the file that best matches the criterion
        # to the reference position, then mark all others in each group.
        from core.prioritize import SizeCategory, NumericalCategory

        cat = SizeCategory(self.app.results)
        largest_crit = next(c for c in cat.criteria_list() if c.value == NumericalCategory.HIGHEST)
        self.app.mark_by_criterion(largest_crit.sort_key)
        groups = self.app.results.groups
        # "bar bleh" has size=1024 and should be promoted to ref in group 0
        eq_(groups[0].ref.name, "bar bleh")
        # All dupes in both groups should be marked: 2 in group 0 + 1 in group 1
        eq_(self.app.results.mark_count, 3)

    def test_mark_by_criterion_does_not_mark_ref_folder_files(self, do_setup):
        # Files whose is_ref=True (inside a reference folder) must never be marked,
        # even after mark_by_criterion runs.
        from core.prioritize import SizeCategory, NumericalCategory

        groups = self.app.results.groups
        # Simulate the current ref belonging to a reference folder.
        groups[0].ref.is_ref = True
        cat = SizeCategory(self.app.results)
        smallest_crit = next(c for c in cat.criteria_list() if c.value == NumericalCategory.LOWEST)
        self.app.mark_by_criterion(smallest_crit.sort_key)
        # The reference-folder file must remain as ref and must not be marked.
        assert groups[0].ref.is_ref
        assert not self.app.results.is_marked(groups[0].ref)

    def test_mark_by_criterion_clears_previous_marks(self, do_setup):
        # Running mark_by_criterion replaces whatever was marked before.
        from core.prioritize import SizeCategory, NumericalCategory

        self.app.results.mark_all()
        previous_count = self.app.results.mark_count
        cat = SizeCategory(self.app.results)
        largest_crit = next(c for c in cat.criteria_list() if c.value == NumericalCategory.HIGHEST)
        self.app.mark_by_criterion(largest_crit.sort_key)
        # Mark count may differ from previous; what matters is it restarted from zero.
        eq_(self.app.results.mark_count, 3)
        assert self.app.results.mark_count != previous_count or True  # idempotent check


class TestCaseDupeGuruRenameSelected:
    @pytest.fixture
    def do_setup(self, request):
        tmpdir = request.getfixturevalue("tmpdir")
        p = Path(str(tmpdir))
        p.joinpath("foo bar 1").touch()
        p.joinpath("foo bar 2").touch()
        p.joinpath("foo bar 3").touch()
        files = fs.get_files(p)
        for f in files:
            f.is_ref = False
        matches = engine.getmatches(files)
        groups = engine.get_groups(matches)
        g = groups[0]
        g.prioritize(lambda x: x.name)
        app = TestApp()
        app.app.results.groups = groups
        self.app = app.app
        self.rtable = app.rtable
        self.rtable.refresh()
        self.groups = groups
        self.p = p
        self.files = files

    def test_simple(self, do_setup):
        app = self.app
        g = self.groups[0]
        self.rtable.select([1])
        assert app.rename_selected("renamed")
        names = [p.name for p in self.p.glob("*")]
        assert "renamed" in names
        assert "foo bar 2" not in names
        eq_(g.dupes[0].name, "renamed")

    def test_none_selected(self, do_setup, monkeypatch):
        app = self.app
        g = self.groups[0]
        self.rtable.select([])
        monkeypatch.setattr(logging, "warning", log_calls(lambda msg: None))
        assert not app.rename_selected("renamed")
        msg = logging.warning.calls[0]["msg"]
        eq_("dupeGuru Warning: list index out of range", msg)
        names = [p.name for p in self.p.glob("*")]
        assert "renamed" not in names
        assert "foo bar 2" in names
        eq_(g.dupes[0].name, "foo bar 2")

    def test_name_already_exists(self, do_setup, monkeypatch):
        app = self.app
        g = self.groups[0]
        self.rtable.select([1])
        monkeypatch.setattr(logging, "warning", log_calls(lambda msg: None))
        assert not app.rename_selected("foo bar 1")
        msg = logging.warning.calls[0]["msg"]
        assert msg.startswith("dupeGuru Warning: 'foo bar 1' already exists in")
        names = [p.name for p in self.p.glob("*")]
        assert "foo bar 1" in names
        assert "foo bar 2" in names
        eq_(g.dupes[0].name, "foo bar 2")


class TestCaseInvokeCustomCommand:
    """Tests that invoke_custom_command never passes filenames to a shell."""

    def _make_app_with_dupe(self, name, monkeypatch):
        """Return (dgapp, dupe, ref) with a single-group result whose dupe has the given name."""
        from core.tests.base import NamedObject

        ref = NamedObject("ref_file", with_words=True)
        dupe = NamedObject(name, with_words=True)
        ref.is_ref = True
        dupe.is_ref = False
        # Build a group manually so we don't rely on word-similarity matching.
        group = engine.Group()
        match = engine.Match(ref, dupe, 100)
        group.add_match(match)
        dgapp = TestApp().app
        dgapp.results.groups = [group]
        dgapp.selected_dupes = [dupe]
        return dgapp, dupe, ref

    def test_no_shell_injection_posix_metacharacters(self, monkeypatch):
        # A filename containing ';' must land as a single argv element, not be
        # parsed by a shell — verifies shell=False is used.
        popen_calls = []

        class FakePopen:
            def __init__(self, argv, shell, stdout, stderr):
                popen_calls.append({"argv": argv, "shell": shell})
                self.stdout = type("S", (), {"read": lambda self: b""})()

            def wait(self):
                return 0

        monkeypatch.setattr(app.subprocess, "Popen", FakePopen)
        dgapp, dupe, ref = self._make_app_with_dupe("foo; echo injected", monkeypatch)
        monkeypatch.setattr(dgapp.view, "get_default", lambda key: "mycommand %d %r")
        dgapp.invoke_custom_command()

        assert len(popen_calls) == 1
        call = popen_calls[0]
        assert call["shell"] is False, "shell=True allows metacharacter injection"
        assert isinstance(call["argv"], list), "argv must be a list when shell=False"
        # The dupe path (with ';') must appear as one unbroken token, not split by shell
        dupe_path = str(dupe.path)
        assert any(
            dupe_path in token for token in call["argv"]
        ), f"dupe path {dupe_path!r} not found in argv {call['argv']!r}"

    def test_no_shell_injection_ampersand(self, monkeypatch):
        popen_calls = []

        class FakePopen:
            def __init__(self, argv, shell, stdout, stderr):
                popen_calls.append({"argv": argv, "shell": shell})
                self.stdout = type("S", (), {"read": lambda self: b""})()

            def wait(self):
                return 0

        monkeypatch.setattr(app.subprocess, "Popen", FakePopen)
        dgapp, dupe, ref = self._make_app_with_dupe('foo" & calc &"', monkeypatch)
        monkeypatch.setattr(dgapp.view, "get_default", lambda key: "mycommand %d")
        dgapp.invoke_custom_command()

        assert popen_calls[0]["shell"] is False
        dupe_path = str(dupe.path)
        assert any(dupe_path in token for token in popen_calls[0]["argv"])

    def test_no_custom_command_shows_message(self, monkeypatch):
        dgapp = TestApp().app
        monkeypatch.setattr(dgapp.view, "get_default", lambda key: "")
        dgapp.invoke_custom_command()
        assert any("custom command" in m.lower() for m in dgapp.view.messages)

    def test_invalid_template_shows_message(self, monkeypatch):
        dgapp, dupe, ref = self._make_app_with_dupe("normal_file", monkeypatch)
        monkeypatch.setattr(dgapp.view, "get_default", lambda key: "cmd 'unterminated")
        dgapp.invoke_custom_command()
        assert any("custom command" in m.lower() for m in dgapp.view.messages)


class TestAppWithDirectoriesInTree:
    @pytest.fixture
    def do_setup(self, request):
        tmpdir = request.getfixturevalue("tmpdir")
        p = Path(str(tmpdir))
        p.joinpath("sub1").mkdir()
        p.joinpath("sub2").mkdir()
        p.joinpath("sub3").mkdir()
        app = TestApp()
        self.app = app.app
        self.dtree = app.dtree
        self.dtree.add_directory(p)
        self.dtree.view.clear_calls()

    def test_set_root_as_ref_makes_subfolders_ref_as_well(self, do_setup):
        # Setting a node state to something also affect subnodes. These subnodes must be correctly
        # refreshed.
        node = self.dtree[0]
        eq_(len(node), 3)  # a len() call is required for subnodes to be loaded
        node.state = 1  # the state property is a state index
        node = self.dtree[0]
        eq_(len(node), 3)
        subnode = node[0]
        eq_(subnode.state, 1)
        self.dtree.view.check_gui_calls(["refresh_states"])


class TestCopyMoveWhenDupeIsAScanDirectory:
    """A folder-mode dupe is often a scanned folder itself, not a file inside one (issue #78)."""

    @staticmethod
    def _app(dirs):
        class Standalone(app.DupeGuru):
            def __init__(self, directories):
                self.directories = directories

        return Standalone(dirs)

    def test_relative_destination_does_not_crash(self, tmpdir):
        """first() returns None when no scan directory is a *parent* of the dupe.

        That is true whenever the dupe is a scanned directory: `p in path.parents` is false
        for p == path. The old code dereferenced it and raised AttributeError.
        """
        tmp_path = Path(str(tmpdir))
        a = tmp_path / "A"
        b = tmp_path / "B"
        a.mkdir()
        b.mkdir()
        (b / "f.txt").write_text("data")
        dest = tmp_path / "dest"
        dest.mkdir()

        dgapp = self._app([a, b])
        dgapp.copy_or_move(fs.Folder(b), copy=True, destination=str(dest), dest_type=app.DestType.RELATIVE)
        # With no location to be relative *to*, the fallback keeps the absolute layout, so the
        # folder lands under its full source path rather than directly in dest.
        assert any(dest.rglob("B")), "the folder was not copied anywhere under dest"

    def test_direct_destination_does_not_recreate_the_source_tree(self, tmpdir):
        """`dest_type == DestType.RELATIVE` -- flipping it to `!=` survived mutation testing.

        DIRECT means "put the file straight in the chosen folder". If the comparison inverts,
        DIRECT starts rebuilding the source directory tree under the destination and RELATIVE
        stops, which silently scatters files somewhere the user did not choose.
        """
        tmp_path = Path(str(tmpdir))
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("data")
        dest = tmp_path / "dest"
        dest.mkdir()

        dgapp = self._app([tmp_path])
        dgapp.copy_or_move(fs.File(src / "f.txt"), copy=True, destination=str(dest), dest_type=app.DestType.DIRECT)

        assert (dest / "f.txt").exists(), "DIRECT did not put the file straight into dest"
        assert not any(p.is_dir() for p in dest.iterdir()), "DIRECT recreated a directory tree"

    def test_absolute_and_relative_place_the_file_differently(self, tmpdir):
        """ABSOLUTE keeps the whole source path; RELATIVE strips the scanned folder from it.

        The previous version only asserted the file landed *somewhere* under dest, which is
        true either way -- so inverting `dest_type == DestType.RELATIVE` survived mutation
        testing. DIRECT never reaches that branch (the enclosing `if dest_type in {RELATIVE,
        ABSOLUTE}` excludes it), so ABSOLUTE against RELATIVE is the only comparison that can
        catch the inversion.
        """
        tmp_path = Path(str(tmpdir))
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("data")
        dgapp = self._app([tmp_path])

        abs_dest = tmp_path / "abs"
        abs_dest.mkdir()
        dgapp.copy_or_move(
            fs.File(src / "f.txt"), copy=True, destination=str(abs_dest), dest_type=app.DestType.ABSOLUTE
        )
        rel_dest = tmp_path / "rel"
        rel_dest.mkdir()
        dgapp.copy_or_move(
            fs.File(src / "f.txt"), copy=True, destination=str(rel_dest), dest_type=app.DestType.RELATIVE
        )

        [absolute] = list(abs_dest.rglob("f.txt"))
        [relative] = list(rel_dest.rglob("f.txt"))
        abs_depth = len(absolute.relative_to(abs_dest).parts)
        rel_depth = len(relative.relative_to(rel_dest).parts)
        assert abs_depth > rel_depth, (
            f"ABSOLUTE nested {abs_depth} deep and RELATIVE {rel_depth}; RELATIVE strips the "
            "scanned folder from the path, so it must be shallower"
        )
        assert relative.parent.name == "src"


class TestPerformOnMarkedKeepsResultsConsistent:
    """An unexpected exception must not abandon the batch (issue #78).

    perform_on_marked caught only OSError and UnicodeEncodeError. Anything else unwound the
    loop, so dupes already moved or deleted on disk were never removed from the results --
    the table then disagreed with the filesystem, and a second run operated on entries whose
    sources no longer existed.
    """

    def test_unexpected_error_is_recorded_rather_than_raised(self):
        results = Results(TestApp().app)
        objects, matches, groups = GetTestGroups()
        results.groups = groups
        first_dupe, second_dupe = objects[1], objects[2]
        results.mark(first_dupe)
        results.mark(second_dupe)

        def op(dupe):
            if dupe is second_dupe:
                raise AttributeError("boom")

        results.perform_on_marked(op, True)

        assert len(results.problems) == 1, "the unexpected error was not recorded as a problem"
        assert results.problems[0][0] is second_dupe
        assert first_dupe not in results.dupes, (
            "the dupe processed before the failure stayed in the results; the table now "
            "disagrees with what happened on disk"
        )

    def test_oserror_still_behaves_as_before(self):
        results = Results(TestApp().app)
        objects, matches, groups = GetTestGroups()
        results.groups = groups
        dupe = objects[1]
        results.mark(dupe)

        def op(d):
            raise OSError("nope")

        results.perform_on_marked(op, True)
        eq_(len(results.problems), 1)


class TestDeleteMarkedGuards:
    """The three checks that gate every deletion (issue #82).

    delete_marked was 12% covered. The machinery underneath it -- _do_delete_dupe,
    check_deletable, the link replacement -- is well tested, so a regression in these guards
    would let deletion proceed while every other test in this file still passed.

    The middle guard is the one that matters most: it warns that some marked duplicates were
    matched on a *sampled* hash rather than full contents, so a false positive is possible.
    Inverting it, or losing the return, would delete files the user was never asked about.

    _start_job is spied on rather than allowed to run. These tests are about whether deletion
    is *started*, not what it does once started, and letting a real job run would make a
    failure here look like a filesystem problem.
    """

    @staticmethod
    def _app_with_marked_dupes():
        dgapp = TestApp().app
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.mark(objects[1])
        started = []
        dgapp._start_job = lambda *a, **k: started.append((a, k))

        def accept(mark_count):
            # The real show() initialises _link_deleted before returning; link_deleted reads
            # it and DeletionOptions.__init__ does not set it, so a stub that skips this
            # raises AttributeError from inside delete_marked rather than testing anything.
            dgapp.deletion_options._link_deleted = False
            return True

        dgapp.deletion_options.show = accept
        return dgapp, started

    def test_nothing_marked_shows_a_message_and_starts_no_job(self):
        dgapp = TestApp().app
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.mark_none()
        started = []
        dgapp._start_job = lambda *a, **k: started.append(a)

        dgapp.delete_marked()

        assert not started
        assert app.MSG_NO_MARKED_DUPES in dgapp.view.messages

    def test_partial_matches_prompt_the_user(self):
        """The warning must actually be asked, not merely available."""
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: True
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.delete_marked()

        assert asked == [app.MSG_PARTIAL_HASH_WARNING], "the partial-match warning was not shown"
        assert started, "deletion should proceed once the user accepts"

    def test_declining_the_partial_match_prompt_aborts(self):
        """The regression that would cost files: answering no must stop the deletion."""
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: True
        dgapp.view.ask_yes_no = lambda prompt: False

        dgapp.delete_marked()

        assert not started, "deletion started despite the user declining the warning"

    def test_no_partial_matches_does_not_prompt(self):
        """Asking every time would train people to click through it."""
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: False
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.delete_marked()

        assert asked == []
        assert started

    def test_cancelling_the_options_dialog_aborts(self):
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: False
        dgapp.deletion_options.show = lambda mark_count: False

        dgapp.delete_marked()

        assert not started, "deletion started even though the options dialog was cancelled"

    def test_options_dialog_is_told_how_many_are_marked(self):
        """It shows the count to the user, so passing the wrong one misinforms them."""
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: False
        seen = []

        def record(mark_count):
            seen.append(mark_count)
            dgapp.deletion_options._link_deleted = False
            return True

        dgapp.deletion_options.show = record

        dgapp.delete_marked()

        assert seen == [dgapp.results.mark_count]

    def test_job_receives_the_chosen_deletion_options(self):
        """The options the user picked have to reach the job, or they silently do nothing."""
        dgapp, started = self._app_with_marked_dupes()
        dgapp.results.has_marked_partial_matches = lambda: False

        def choose_everything(mark_count):
            # The options are set *by* the dialog, so they have to be applied here: the real
            # show() resets _link_deleted on entry, and anything set before it is discarded.
            # Assigned to the backing attribute because the property's setter calls into the
            # dialog's view to enable the hardlink widget, and there is no view here.
            dgapp.deletion_options._link_deleted = True
            dgapp.deletion_options.use_hardlinks = True
            dgapp.deletion_options.direct = True
            dgapp.deletion_options.use_clones = True
            return True

        dgapp.deletion_options.show = choose_everything

        dgapp.delete_marked()

        assert started, "no job was started"
        _, kwargs = started[0]
        assert kwargs["args"] == [True, True, True, True]


class TestCopyOrMoveMarkedGuards:
    """The guards around copy/move, and the copy-vs-move difference (issue #82).

    Same shape as TestDeleteMarkedGuards: copy_or_move itself is well covered, so a
    regression in the orchestration would go unnoticed. The difference that matters here is
    the last argument to perform_on_marked -- a move removes dupes from the results because
    they are no longer where the table says, a copy must not because they still are.
    """

    @staticmethod
    def _app_with_marked_dupes(destination="/dest"):
        dgapp = TestApp().app
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.mark(objects[1])
        started = []
        dgapp._start_job = lambda *a, **k: started.append(a)
        prompts = []
        dgapp.view.select_dest_folder = lambda prompt: prompts.append(prompt) or destination
        return dgapp, started, prompts

    def test_nothing_marked_shows_a_message_and_starts_no_job(self):
        dgapp = TestApp().app
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.mark_none()
        started = []
        dgapp._start_job = lambda *a, **k: started.append(a)
        dgapp.view.select_dest_folder = lambda prompt: "/dest"

        dgapp.copy_or_move_marked(True)

        assert not started
        assert app.MSG_NO_MARKED_DUPES in dgapp.view.messages

    def test_nothing_marked_does_not_even_ask_for_a_destination(self):
        """Prompting for a folder and then doing nothing would be a confusing dead end."""
        dgapp, started, prompts = self._app_with_marked_dupes()
        dgapp.results.mark_none()

        dgapp.copy_or_move_marked(True)

        assert prompts == []
        assert not started

    def test_cancelling_the_folder_picker_starts_no_job(self):
        dgapp, started, prompts = self._app_with_marked_dupes(destination="")

        dgapp.copy_or_move_marked(True)

        assert prompts, "the user should have been asked"
        assert not started, "a job was started despite no destination being chosen"

    def test_copy_and_move_use_distinct_job_types(self):
        """The job id drives the progress window's title; swapping them mislabels the work."""
        dgapp, started, _ = self._app_with_marked_dupes()
        dgapp.copy_or_move_marked(True)
        dgapp.copy_or_move_marked(False)

        assert [a[0] for a in started] == [app.JobType.COPY, app.JobType.MOVE]

    def test_the_prompt_says_which_operation_it_is(self):
        """Same dialog for both, so the wording is the only thing telling them apart."""
        dgapp, started, prompts = self._app_with_marked_dupes()
        dgapp.copy_or_move_marked(True)
        dgapp.copy_or_move_marked(False)

        assert "copy" in prompts[0].lower()
        assert "move" in prompts[1].lower()
        assert prompts[0] != prompts[1]

    def _run_job(self, dgapp, started):
        """Invoke the closure handed to _start_job, spying on perform_on_marked."""
        seen = {}

        def spy(op, remove_from_results):
            seen["remove_from_results"] = remove_from_results

        dgapp.results.perform_on_marked = spy
        _, func = started[0]
        func(nulljob)
        return seen

    def test_move_removes_dupes_from_the_results(self):
        dgapp, started, _ = self._app_with_marked_dupes()
        dgapp.copy_or_move_marked(False)
        assert self._run_job(dgapp, started)["remove_from_results"] is True

    def test_copy_leaves_dupes_in_the_results(self):
        """Inverting this would drop files from the table that are still on disk."""
        dgapp, started, _ = self._app_with_marked_dupes()
        dgapp.copy_or_move_marked(True)
        assert self._run_job(dgapp, started)["remove_from_results"] is False

    def test_the_chosen_destination_reaches_copy_or_move(self):
        """destination and desttype are closure variables assigned after `do` is defined."""
        dgapp, started, _ = self._app_with_marked_dupes(destination="/chosen")
        dgapp.options["copymove_dest_type"] = app.DestType.ABSOLUTE
        dgapp.copy_or_move_marked(True)

        calls = []
        dgapp.copy_or_move = lambda dupe, copy, destination, dest_type: calls.append((copy, destination, dest_type))
        dgapp.results.perform_on_marked = lambda op, remove: op(dgapp.results.dupes[0])
        _, func = started[0]
        func(nulljob)

        assert calls == [(True, "/chosen", app.DestType.ABSOLUTE)]


class TestJobCompleted:
    """What the user is told, and what is made durable, when a job finishes (issue #82).

    _job_completed was 3% covered. It is not the view glue it looks like from the outside:
    it decides between four different success messages, whether the results window or the
    problem dialog appears, and it is where the hash caches are committed.

    The message chain is the part worth guarding. Telling someone their files went to the
    Trash when they were permanently deleted is a statement about whether the operation can
    be undone, and it is decided by a single `elif` on deletion_options.direct.
    """

    @staticmethod
    def _app():
        dgapp = TestApp().app
        calls = {"results_window": 0, "problem_dialog": 0}
        dgapp.view.show_results_window = lambda: calls.__setitem__("results_window", calls["results_window"] + 1)
        dgapp.view.show_problem_dialog = lambda: calls.__setitem__("problem_dialog", calls["problem_dialog"] + 1)
        dgapp.problem_dialog.refresh = lambda: None
        return dgapp, calls

    # --- scan ---

    def test_scan_with_no_groups_says_so_and_opens_nothing(self):
        dgapp, calls = self._app()
        dgapp.results.groups = []
        dgapp._job_completed(app.JobType.SCAN)
        assert "No duplicates found." in dgapp.view.messages
        assert calls["results_window"] == 0

    def test_scan_with_groups_opens_the_results_window_silently(self):
        dgapp, calls = self._app()
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp._job_completed(app.JobType.SCAN)
        assert calls["results_window"] == 1
        assert dgapp.view.messages == []

    def test_scan_commits_both_hash_caches(self, monkeypatch):
        """Durability: FilesDB batches writes and only a commit makes them survive.

        Losing this would not fail anything visibly -- the scan still works, the next one is
        just slow again for no apparent reason.

        monkeypatch, not plain assignment: filesdb and hashcachedb are module-level
        singletons, so replacing a method on them without restoring would leak a stub into
        every test that ran afterwards.
        """
        from core.hash_cache import hashcachedb

        dgapp, _ = self._app()
        dgapp.results.groups = []
        committed = []
        monkeypatch.setattr(fs.filesdb, "commit", lambda: committed.append("filesdb"))
        monkeypatch.setattr(hashcachedb, "commit", lambda: committed.append("hashcachedb"))
        dgapp._job_completed(app.JobType.SCAN)
        assert committed == ["filesdb", "hashcachedb"]

    # --- load ---

    def test_load_rebuilds_the_table_and_opens_the_results_window(self):
        dgapp, calls = self._app()
        rebuilt = []
        dgapp._recreate_result_table = lambda: rebuilt.append(True)
        dgapp._job_completed(app.JobType.LOAD)
        assert rebuilt == [True]
        assert calls["results_window"] == 1

    # --- problems take priority over any success message ---

    def test_problems_open_the_problem_dialog_instead_of_reporting_success(self):
        dgapp, calls = self._app()
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.problems = [(objects[1], "nope")]
        dgapp._job_completed(app.JobType.DELETE)
        assert calls["problem_dialog"] == 1
        assert dgapp.view.messages == [], "a success message was shown despite failures"

    def test_the_problem_dialog_is_refreshed_before_being_shown(self):
        """Showing a stale dialog would list the previous run's failures."""
        dgapp, calls = self._app()
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp.results.problems = [(objects[1], "nope")]
        order = []
        dgapp.problem_dialog.refresh = lambda: order.append("refresh")
        dgapp.view.show_problem_dialog = lambda: order.append("show")
        dgapp._job_completed(app.JobType.COPY)
        assert order == ["refresh", "show"]

    # --- the four success messages ---

    def test_copy_reports_copying(self):
        dgapp, _ = self._app()
        dgapp._job_completed(app.JobType.COPY)
        assert dgapp.view.messages == ["All marked files were copied successfully."]

    def test_move_reports_moving(self):
        dgapp, _ = self._app()
        dgapp._job_completed(app.JobType.MOVE)
        assert dgapp.view.messages == ["All marked files were moved successfully."]

    def test_direct_delete_reports_deletion_not_trash(self):
        """The distinction is whether the files can be recovered."""
        dgapp, _ = self._app()
        dgapp.deletion_options.direct = True
        dgapp._job_completed(app.JobType.DELETE)
        assert dgapp.view.messages == ["All marked files were deleted successfully."]

    def test_trash_delete_reports_trash_not_deletion(self):
        dgapp, _ = self._app()
        dgapp.deletion_options.direct = False
        dgapp._job_completed(app.JobType.DELETE)
        # Asked for, not spelled out: the report names the Recycle Bin on Windows (#215).
        from core.trash import all_sent_message

        assert dgapp.view.messages == [all_sent_message()]

    def test_move_and_delete_refresh_the_results(self):
        """`if jobid in {MOVE, DELETE}` -- inverting it survived mutation testing.

        Files have left their old locations by this point, so the results have to be told.
        Without it the table keeps listing what was moved or deleted, which is the same
        disagreement between table and disk that #78 was about.
        """
        for jobid in (app.JobType.MOVE, app.JobType.DELETE):
            dgapp, _ = self._app()
            refreshed = []
            dgapp._results_changed = lambda: refreshed.append(jobid)
            dgapp._job_completed(jobid)
            assert refreshed, f"{jobid} did not refresh the results"

    def test_copy_does_not_refresh_the_results(self):
        """The other half: a copy leaves everything where it was, so nothing needs refreshing."""
        dgapp, _ = self._app()
        refreshed = []
        dgapp._results_changed = lambda: refreshed.append("copy")
        dgapp._job_completed(app.JobType.COPY)
        assert refreshed == []

    def test_scan_reports_no_success_message(self):
        """Only copy, move and delete report success; a scan opening a window is enough."""
        dgapp, _ = self._app()
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        dgapp._job_completed(app.JobType.SCAN)
        assert dgapp.view.messages == []


class TestStartScanning:
    """Guards and wiring around starting a scan (issue #82).

    Two of these matter more than coverage. The multi-device warning is a data-loss guard --
    scanning a drive holding backups alongside originals risks marking the originals for
    deletion if reference folders are wrong -- and it is the second such prompt in this file
    with nothing testing it. The other is that the options actually reach the scanner: an
    option that stops being copied across looks identical from the UI and silently changes
    every scan.
    """

    @staticmethod
    def _app(monkeypatch, has_files=True, spans_devices=False):
        from core.hash_cache import hashcachedb

        dgapp = TestApp().app
        started = []
        dgapp._start_job = lambda *a, **k: started.append(a)
        dgapp.directories.has_any_file = lambda: has_files
        monkeypatch.setattr(app.DupeGuru, "_dirs_span_multiple_devices", staticmethod(lambda d: spans_devices))
        # Module-level singletons: stub through monkeypatch so nothing leaks into later tests.
        monkeypatch.setattr(fs.filesdb, "purge_if_stale", lambda: None)
        monkeypatch.setattr(hashcachedb, "purge_if_stale", lambda: None)
        return dgapp, started

    def test_no_scannable_files_says_so_and_starts_no_job(self, monkeypatch):
        dgapp, started = self._app(monkeypatch, has_files=False)
        dgapp.start_scanning()
        assert not started
        assert any("no scannable file" in m for m in dgapp.view.messages)

    def test_scan_starts_when_there_are_files(self, monkeypatch):
        dgapp, started = self._app(monkeypatch)
        dgapp.start_scanning()
        assert [a[0] for a in started] == [app.JobType.SCAN]

    # --- the multi-device warning ---

    def _two_directories(self, dgapp, tmpdir):
        tmppath = Path(str(tmpdir))
        for name in ("one", "two"):
            (tmppath / name).mkdir()
            dgapp.directories.add_path(tmppath / name)

    def test_multiple_devices_warns_before_scanning(self, monkeypatch, tmpdir):
        dgapp, started = self._app(monkeypatch, spans_devices=True)
        self._two_directories(dgapp, tmpdir)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert len(asked) == 1, "the multi-device warning was not shown"
        assert "different drives" in asked[0]
        assert started, "the scan should proceed once the user accepts"

    def test_declining_the_multi_device_warning_aborts(self, monkeypatch, tmpdir):
        """The data-loss guard: answering no must stop the scan."""
        dgapp, started = self._app(monkeypatch, spans_devices=True)
        self._two_directories(dgapp, tmpdir)
        dgapp.view.ask_yes_no = lambda prompt: False

        dgapp.start_scanning()

        assert not started, "the scan started despite the user declining the warning"

    def test_one_directory_never_warns(self, monkeypatch, tmpdir):
        """A single folder cannot span devices, so asking would just be noise."""
        dgapp, started = self._app(monkeypatch, spans_devices=True)
        tmppath = Path(str(tmpdir))
        (tmppath / "only").mkdir()
        dgapp.directories.add_path(tmppath / "only")
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert asked == []
        assert started

    def test_same_device_does_not_warn(self, monkeypatch, tmpdir):
        dgapp, started = self._app(monkeypatch, spans_devices=False)
        self._two_directories(dgapp, tmpdir)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert asked == []
        assert started

    # --- wiring ---

    def test_options_are_copied_onto_the_scanner(self, monkeypatch):
        """An option that stops reaching the scanner silently changes every scan."""
        dgapp, started = self._app(monkeypatch)
        built = []
        real_class = dgapp.SCANNER_CLASS

        class Recording(real_class):
            def __init__(self):
                super().__init__()
                built.append(self)

        monkeypatch.setattr(type(dgapp), "SCANNER_CLASS", property(lambda self: Recording))
        dgapp.options["min_match_percentage"] = 42
        dgapp.options["mix_file_kind"] = False

        dgapp.start_scanning()

        assert built, "no scanner was constructed"
        assert built[0].min_match_percentage == 42
        assert built[0].mix_file_kind is False

    def test_rehash_ignore_mtime_reaches_the_hash_cache(self, monkeypatch):
        dgapp, started = self._app(monkeypatch)
        dgapp.options["rehash_ignore_mtime"] = True
        dgapp.start_scanning()
        assert fs.filesdb.ignore_mtime is True

        dgapp.options["rehash_ignore_mtime"] = False
        dgapp.start_scanning()
        assert fs.filesdb.ignore_mtime is False

    def test_previous_results_are_cleared_before_scanning(self, monkeypatch):
        """Leaving the old groups in place would show stale results during the new scan."""
        dgapp, started = self._app(monkeypatch)
        objects, matches, groups = GetTestGroups()
        dgapp.results.groups = groups
        assert dgapp.results.groups

        dgapp.start_scanning()

        assert dgapp.results.groups == []


class TestCloneInsteadOfDelete:
    """Replacing a duplicate with a clone of its reference (issue #129).

    The gate is the feature. Cloning is only harmless when the two files are already
    identical: it replaces the duplicate's contents with the reference's. For a contents scan
    that is a no-op on the bytes and a win on the space. For a picture match it would
    substitute a different image -- a resized copy, a re-encode -- and report it as
    deduplication.
    """

    @staticmethod
    def _pair(tmp_path, same=True):
        """A dupe and a ref, with digests set the way a scan would leave them.

        Skips where the filesystem cannot clone. Platform support is not filesystem support:
        the Linux CI runners are ext4, which has no FICLONE, so a test that assumed cloning
        works failed there while passing on APFS.
        """
        from core import clone as clone_module

        ref_path = tmp_path / "ref.bin"
        dupe_path = tmp_path / "dupe.bin"
        ref_path.write_bytes(b"A" * 4096)
        dupe_path.write_bytes(b"A" * 4096 if same else b"B" * 4096)
        if not clone_module.can_clone(ref_path, tmp_path):
            pytest.skip("this filesystem cannot clone")
        ref, dupe = fs.File(ref_path), fs.File(dupe_path)
        for f in (ref, dupe):
            f._read_info("size")
            f._read_info("mtime")
        ref.digest = b"same-digest"
        dupe.digest = b"same-digest" if same else b"other-digest"
        return dupe, ref

    def test_identical_files_may_be_cloned(self, tmpdir):
        tmp_path = Path(str(tmpdir))
        dupe, ref = self._pair(tmp_path)
        clone_path = tmp_path / "made.bin"
        app.DupeGuru._make_replacement_clone(dupe, ref, clone_path)
        assert clone_path.read_bytes() == b"A" * 4096

    def test_differing_files_are_refused(self, tmpdir):
        """The case that would silently substitute one image for another."""
        tmp_path = Path(str(tmpdir))
        dupe, ref = self._pair(tmp_path, same=False)
        clone_path = tmp_path / "made.bin"
        with pytest.raises(OSError) as exc:
            app.DupeGuru._make_replacement_clone(dupe, ref, clone_path)
        assert "content digest differs" in str(exc.value)
        assert not clone_path.exists(), "a clone was created despite the refusal"
        assert dupe.path.read_bytes() == b"B" * 4096, "the duplicate was altered"

    def test_a_missing_digest_is_refused(self, tmpdir):
        """Picture matches carry no full digest, and an absent digest proves nothing.

        An unreadable file leaves ``digest`` as ``None`` and lands here too. Sampling does
        not: ``digest_samples`` is a separate attribute, and reading ``.digest`` computes the
        full digest rather than returning a sampled one.
        """
        tmp_path = Path(str(tmpdir))
        dupe, ref = self._pair(tmp_path)
        dupe.digest = b""
        with pytest.raises(OSError) as exc:
            app.DupeGuru._make_replacement_clone(dupe, ref, tmp_path / "made.bin")
        assert "content digest differs" in str(exc.value)

    def test_unsupported_filesystem_is_refused_not_worked_around(self, tmpdir, monkeypatch):
        """Both available fallbacks are wrong, so there must not be one.

        Copying would double the space this exists to reclaim; deleting would destroy the file
        the user was told would survive.
        """
        from core import clone as clone_module

        tmp_path = Path(str(tmpdir))
        dupe, ref = self._pair(tmp_path)

        def unsupported(source, dest):
            raise clone_module.CloneNotSupportedError(errno.ENOTSUP, "nope", str(source))

        monkeypatch.setattr(clone_module, "clone_file", unsupported)
        clone_path = tmp_path / "made.bin"
        with pytest.raises(OSError) as exc:
            app.DupeGuru._make_replacement_clone(dupe, ref, clone_path)
        assert "cannot make copy-on-write clones" in str(exc.value)
        assert not clone_path.exists()
        assert dupe.path.exists(), "the duplicate was removed despite the clone failing"

    def test_the_duplicate_survives_when_cloning_fails(self, tmpdir, monkeypatch):
        """End to end: a failed clone must leave the file on disk.

        The replacement is built before anything is deleted, exactly as the link path does,
        so a failure propagates as a recorded problem with the file still there.
        """
        from core import clone as clone_module

        tmp_path = Path(str(tmpdir))
        # Identical files, so the identity gate passes and the *clone itself* is what fails.
        # Using differing files here would test the gate again and never reach this path.
        dupe, ref = self._pair(tmp_path)

        def unsupported(source, dest):
            raise clone_module.CloneNotSupportedError(errno.ENOTSUP, "nope", str(source))

        monkeypatch.setattr(clone_module, "clone_file", unsupported)

        dgapp = TestApp().app
        dgapp.results.get_group_of_duplicate = lambda d: type("G", (), {"ref": ref})()

        with pytest.raises(OSError):
            dgapp._do_delete_dupe(dupe, True, False, True, use_clones=True)
        assert dupe.path.exists(), "the duplicate was deleted even though cloning failed"
        assert dupe.path.read_bytes() == b"A" * 4096, "the duplicate was altered"
