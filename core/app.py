# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import cProfile
import datetime
import errno
import os
import os.path as op
import logging
import shlex
import stat
import subprocess
import sys
import shutil
from pathlib import Path

from send2trash import send2trash
from hscommon.jobprogress import job
from hscommon.notify import Broadcaster
from hscommon.conflict import smart_move, smart_copy
from hscommon.gui.progress_window import ProgressWindow
from hscommon.util import delete_if_empty, first, escape, nonone, allsame
from hscommon.trans import tr
from hscommon import desktop

from core import se, me, pe, clone
from core.pe.photo import get_delta_dimensions
from core.util import cmp_value, fix_surrogate_encoding
from core import directories, results, export, fs, prioritize
from core.ignore import IgnoreList
from core.exclude import ExcludeDict as ExcludeList
from core.scanner import ScanType
from core.gui.deletion_options import DeletionOptions
from core.scan_profile import ProfileStore, ScanProfile, ScanProfileError
from core.gui.details_panel import DetailsPanel
from core.gui.directory_tree import DirectoryTree
from core.gui.ignore_list_dialog import IgnoreListDialog
from core.gui.exclude_list_dialog import ExcludeListDialogCore
from core.gui.problem_dialog import ProblemDialog
from core.gui.stats_label import StatsLabel

HAD_FIRST_LAUNCH_PREFERENCE = "HadFirstLaunch"
DEBUG_MODE_PREFERENCE = "DebugMode"

MSG_NO_MARKED_DUPES = tr("There are no marked duplicates. Nothing has been done.")
MSG_NO_SELECTED_DUPES = tr("There are no selected duplicates. Nothing has been done.")
MSG_PARTIAL_HASH_WARNING = tr(
    "Some of the marked duplicates were matched using a partial (sampled) hash, not a full "
    "file comparison. They are probable duplicates but a false positive is possible.\n\n"
    "Do you want to continue with deletion?"
)
MSG_MANY_FILES_TO_OPEN = tr(
    "You're about to open many files at once. Depending on what those "
    "files are opened with, doing so can create quite a mess. Continue?"
)


class DestType:
    DIRECT = 0
    RELATIVE = 1
    ABSOLUTE = 2


class JobType:
    SCAN = "job_scan"
    LOAD = "job_load"
    MOVE = "job_move"
    COPY = "job_copy"
    DELETE = "job_delete"


class AppMode:
    STANDARD = 0
    MUSIC = 1
    PICTURE = 2


JOBID2TITLE = {
    JobType.SCAN: tr("Scanning for duplicates"),
    JobType.LOAD: tr("Loading"),
    JobType.MOVE: tr("Moving"),
    JobType.COPY: tr("Copying"),
    JobType.DELETE: tr("Sending to Trash"),
}


class DeleteStatus:
    """Outcome of re-validating a file just before deleting it.

    OK and GONE both mean "no problem to report"; the rest are refusals.
    """

    OK = "ok"  # present and unchanged since the scan
    GONE = "gone"  # path no longer exists; deleting it is a no-op, not an error
    SYMLINK = "symlink"  # path is now a symlink, which scans exclude
    UNREADABLE = "unreadable"  # stat() failed
    CHANGED = "changed"  # size or mtime differs from what the scan recorded


# 2-second tolerance covers FAT32's 2-second mtime resolution and NTFS rounding.
_MTIME_TOLERANCE = 2


def _aggregate_size(path):
    """Total size of every file under *path*, matching how :class:`~core.fs.Folder` sizes itself.

    Symlinks are skipped, because ``Folder`` never counts them: its files come from
    ``fs.get_files``, and ``File.can_handle`` rejects anything symlinked. Counting them here
    would inflate the total against a folder that contains one and refuse the deletion --
    the same class of mismatch this function was fixed for. ``os.walk`` likewise does not
    descend into symlinked directories, matching ``Folder.subfolders``.

    Unreadable entries are skipped rather than raising. A permission error partway through
    would otherwise turn "verify before deleting" into "cannot delete at all", which is the
    failure this whole function exists to prevent.
    """
    total = 0
    for dirpath, _, filenames in os.walk(str(path)):
        for name in filenames:
            try:
                st = os.lstat(op.join(dirpath, name))
                if not stat.S_ISLNK(st.st_mode):
                    total += st.st_size
            except OSError:
                pass
    return total


def _is_byte_identical(dupe, ref):
    """Whether *dupe* and *ref* are provably the same bytes.

    Cloning replaces a duplicate with a clone of its reference, which is only harmless when
    the two are already identical. That holds for a contents scan, where a match *means*
    equal digests. It does not hold for picture matching, where two files can score 100%
    because their block signatures agree while the files differ -- a resized copy, a
    re-encode, a different crop. Replacing one of those would substitute a different image
    and call it deduplication.

    So this compares full digests rather than trusting the match. A digest that is missing or
    only partial is not proof, and is treated as a refusal: sampled hashing compares three
    chunks, and three matching chunks are not a guarantee of the rest.
    """
    dupe_digest = getattr(dupe, "digest", b"")
    ref_digest = getattr(ref, "digest", b"")
    if not dupe_digest or not ref_digest:
        return False
    return dupe_digest == ref_digest


def check_deletable(path, expected_size, expected_mtime):
    """Decide whether *path* can still be deleted, without deleting anything.

    Returns ``(status, message)`` where status is a :class:`DeleteStatus` value and message
    is a human-readable explanation (empty when the status is OK or GONE).

    This is the single source of truth for "would this deletion actually happen". The
    deleter raises on whatever this refuses, and the planner reports the same statuses
    without touching the filesystem, so a plan cannot disagree with the deletion it
    predicts -- a plan computed by different logic than the deletion is worse than no plan.
    """
    if not path.exists():
        return DeleteStatus.GONE, ""
    # Symlinks are excluded from scans; if the path is now a symlink it was replaced
    # after the scan and we refuse to act on it.
    if path.is_symlink():
        return (
            DeleteStatus.SYMLINK,
            tr("'{}' is a symlink. Deletion through symlinks is not permitted.").format(str(path)),
        )
    # Re-validate size and mtime against values recorded at scan time. A mismatch means the
    # file changed between scan and delete; refusing prevents deleting something the user
    # never actually reviewed as a duplicate.
    try:
        st = path.stat()
    except OSError as e:
        return (
            DeleteStatus.UNREADABLE,
            tr("Could not verify '{}' before deletion: {}").format(str(path), e),
        )
    # A directory's st_size is the size of its own directory entry -- 128 bytes on APFS, 4096
    # on ext4 -- while Folder.size is the aggregate of everything underneath it. Comparing the
    # two can never match, so every folder was classified CHANGED and folder-mode deletion was
    # impossible. Recompute the comparable quantity rather than dropping the check: a folder
    # whose contents changed after the scan must still be refused.
    actual_size = _aggregate_size(path) if path.is_dir() else st.st_size
    if actual_size != expected_size or abs(st.st_mtime - expected_mtime) > _MTIME_TOLERANCE:
        return (
            DeleteStatus.CHANGED,
            tr(
                "'{}' was skipped: the file changed since the last scan (size or modification "
                "time differs). Re-scan to refresh results."
            ).format(str(path)),
        )
    return DeleteStatus.OK, ""


class DupeGuru(Broadcaster):
    """Holds everything together.

    Instantiated once per running application, it holds a reference to every high-level object
    whose reference needs to be held: :class:`~core.results.Results`,
    :class:`~core.directories.Directories`, :mod:`core.gui` instances, etc..

    It also hosts high level methods and acts as a coordinator for all those elements. This is why
    some of its methods seem a bit shallow, like for example :meth:`mark_all` and
    :meth:`remove_duplicates`. These methos are just proxies for a method in :attr:`results`, but
    they are also followed by a notification call which is very important if we want GUI elements
    to be correctly notified of a change in the data they're presenting.

    .. attribute:: directories

        Instance of :class:`~core.directories.Directories`. It holds the current folder selection.

    .. attribute:: results

        Instance of :class:`core.results.Results`. Holds the results of the latest scan.

    .. attribute:: selected_dupes

        List of currently selected dupes from our :attr:`results`. Whenever the user changes its
        selection at the UI level, :attr:`result_table` takes care of updating this attribute, so
        you can trust that it's always up-to-date.

    .. attribute:: result_table

        Instance of :mod:`meta-gui <core.gui>` table listing the results from :attr:`results`
    """

    # --- View interface
    # get_default(key_name)
    # set_default(key_name, value)
    # show_message(msg)
    # open_url(url)
    # open_path(path)
    # reveal_path(path)
    # ask_yes_no(prompt) --> bool
    # create_results_window()
    # show_results_window()
    # show_problem_dialog()
    # select_dest_folder(prompt: str) --> str
    # select_dest_file(prompt: str, ext: str) --> str

    NAME = PROMPT_NAME = "dupeGuru"

    def __init__(self, view, portable=False):
        if view.get_default(DEBUG_MODE_PREFERENCE):
            logging.getLogger().setLevel(logging.DEBUG)
            logging.debug("Debug mode enabled")
        Broadcaster.__init__(self)
        self.view = view
        self.appdata = desktop.special_folder_path(desktop.SpecialFolder.APPDATA, portable=portable)
        # Optional core.pe.match_cache.MatchCache, attached by the front end when the user
        # opts in. None means picture matching is recomputed on every scan.
        self.picture_match_cache = None
        if not op.exists(self.appdata):
            os.makedirs(self.appdata)
        self.app_mode = AppMode.STANDARD
        self.discarded_file_count = 0
        # Set by the scanner when the full_verify option is on; both stay 0 otherwise.
        self.discarded_partial_count = 0
        self.verified_partial_count = 0
        self.exclude_list = ExcludeList()
        hash_cache_file = op.join(self.appdata, "hash_cache.db")
        fs.filesdb.connect(hash_cache_file)
        from core.hash_cache import hashcachedb

        hashcachedb.connect(op.join(self.appdata, "hash_cache2.db"))
        self.directories = directories.Directories(self.exclude_list)
        self.scan_profiles = ProfileStore()
        self.results = results.Results(self)
        self.ignore_list = IgnoreList()
        # In addition to "app-level" options, this dictionary also holds options that will be
        # sent to the scanner. They don't have default values because those defaults values are
        # defined in the scanner class.
        self.options = {
            "escape_filter_regexp": True,
            "clean_empty_dirs": False,
            "ignore_hardlink_matches": False,
            "copymove_dest_type": DestType.RELATIVE,
            "include_exists_check": True,
            "rehash_ignore_mtime": False,
        }
        self.selected_dupes = []
        self.details_panel = DetailsPanel(self)
        self.directory_tree = DirectoryTree(self)
        self.problem_dialog = ProblemDialog(self)
        self.ignore_list_dialog = IgnoreListDialog(self)
        self.exclude_list_dialog = ExcludeListDialogCore(self)
        self.stats_label = StatsLabel(self)
        self.result_table = None
        self.deletion_options = DeletionOptions()
        self.progress_window = ProgressWindow(self._job_completed, self._job_error)
        children = [self.directory_tree, self.stats_label, self.details_panel]
        for child in children:
            child.connect()

    # --- Private
    def _recreate_result_table(self):
        if self.result_table is not None:
            self.result_table.disconnect()
        if self.app_mode == AppMode.PICTURE:
            self.result_table = pe.result_table.ResultTable(self)
        elif self.app_mode == AppMode.MUSIC:
            self.result_table = me.result_table.ResultTable(self)
        else:
            self.result_table = se.result_table.ResultTable(self)
        self.result_table.connect()
        self.view.create_results_window()

    def _get_picture_cache_path(self):
        cache_name = "cached_pictures.db"
        return op.join(self.appdata, cache_name)

    def _get_dupe_sort_key(self, dupe, get_group, key, delta):
        if self.app_mode in (AppMode.MUSIC, AppMode.PICTURE) and key == "folder_path":
            dupe_folder_path = getattr(dupe, "display_folder_path", dupe.folder_path)
            return str(dupe_folder_path).lower()
        if self.app_mode == AppMode.PICTURE and delta and key == "dimensions":
            r = cmp_value(dupe, key)
            ref_value = cmp_value(get_group().ref, key)
            return get_delta_dimensions(r, ref_value)
        if key == "marked":
            return self.results.is_marked(dupe)
        if key == "percentage":
            m = get_group().get_match_of(dupe)
            return m.percentage
        elif key == "dupe_count":
            return 0
        else:
            result = cmp_value(dupe, key)
        if delta:
            refval = cmp_value(get_group().ref, key)
            if key in self.result_table.DELTA_COLUMNS:
                result -= refval
            else:
                same = cmp_value(dupe, key) == refval
                result = (same, result)
        return result

    def _get_group_sort_key(self, group, key):
        if self.app_mode in (AppMode.MUSIC, AppMode.PICTURE) and key == "folder_path":
            dupe_folder_path = getattr(group.ref, "display_folder_path", group.ref.folder_path)
            return str(dupe_folder_path).lower()
        if key == "percentage":
            return group.percentage
        if key == "dupe_count":
            return len(group)
        if key == "marked":
            return len([dupe for dupe in group.dupes if self.results.is_marked(dupe)])
        return cmp_value(group.ref, key)

    def _do_delete(self, j, link_deleted, use_hardlinks, direct_deletion, use_clones=False):
        def op(dupe):
            j.add_progress()
            return self._do_delete_dupe(dupe, link_deleted, use_hardlinks, direct_deletion, use_clones)

        j.start_job(self.results.mark_count)
        self.results.perform_on_marked(op, True)

    @staticmethod
    def _dirs_span_multiple_devices(directories):
        """Return True if the configured directories live on more than one storage device."""
        devices = set()
        for path in directories:
            try:
                devices.add(os.stat(path).st_dev)
            except OSError:
                pass
            if len(devices) > 1:
                return True
        return False

    def _do_delete_dupe(self, dupe, link_deleted, use_hardlinks, direct_deletion, use_clones=False):
        # Shared with the planner: see check_deletable. Keep the decision there, not here.
        status, message = check_deletable(dupe.path, dupe.size, dupe.mtime)
        if status == DeleteStatus.GONE:
            return
        if status != DeleteStatus.OK:
            raise OSError(message)
        str_path = str(dupe.path)
        link_tmp = None
        if link_deleted:
            group = self.results.get_group_of_duplicate(dupe)
            ref = group.ref
            # Build the replacement link *before* destroying anything, at a temporary
            # name beside the original. Creating it first is the whole point: if it
            # cannot be made -- no symlink privilege on Windows, a cross-device
            # hardlink -- the original is still on disk and the error propagates to
            # perform_on_marked, which records a problem and leaves the file marked.
            # The previous order deleted first and, on the Windows privilege error,
            # swallowed the exception, so the file was gone, no link replaced it, and
            # the operation was reported as a success.
            link_tmp = self._unused_link_path(str_path)
            if use_clones:
                self._make_replacement_clone(dupe, ref, link_tmp)
            else:
                self._make_replacement_link(ref.path, link_tmp, use_hardlinks)

        logging.debug("Sending '%s' to trash", dupe.path)
        try:
            if direct_deletion:
                if op.isdir(str_path):
                    shutil.rmtree(str_path)
                else:
                    os.remove(str_path)
            else:
                send2trash(str_path)  # Raises OSError when there's a problem
        except Exception:
            if link_tmp is not None:
                try:
                    link_tmp.unlink()
                except OSError:
                    logging.warning("Could not clean up temporary link '%s'", link_tmp)
            raise

        if link_tmp is not None:
            # The original is gone, so this moves the link into its place.
            os.replace(str(link_tmp), str_path)
        self.clean_empty_dirs(dupe.path.parent)

    @staticmethod
    def _unused_link_path(str_path):
        """Return a free path beside ``str_path`` to build a replacement link at."""
        candidate = Path(str_path + ".dupeguru-link")
        counter = 0
        # exists() is False for a broken symlink, so is_symlink() has to be checked too.
        while candidate.exists() or candidate.is_symlink():
            counter += 1
            candidate = Path(f"{str_path}.dupeguru-link{counter}")
        return candidate

    @staticmethod
    def _make_replacement_clone(dupe, ref, clone_path):
        """Create a copy-on-write clone of *ref* at *clone_path*.

        Refuses unless the two files are provably identical, and refuses when the filesystem
        cannot clone rather than falling back. Both fallbacks available here are wrong: a copy
        would double the space this exists to reclaim, and a delete would destroy the file the
        user was told would survive.
        """
        if not _is_byte_identical(dupe, ref):
            raise OSError(
                tr(
                    "'{}' was skipped: it is not byte-for-byte identical to its reference, so "
                    "replacing it with a clone would change its contents. Cloning is only "
                    "possible for exact duplicates."
                ).format(str(dupe.path))
            )
        try:
            clone.clone_file(ref.path, clone_path)
        except clone.CloneNotSupportedError as e:
            raise OSError(
                tr(
                    "'{}' was skipped: this filesystem cannot make copy-on-write clones, or "
                    "the two files are on different volumes. Nothing was deleted."
                ).format(str(dupe.path))
            ) from e

    @staticmethod
    def _make_replacement_link(source, link_path, use_hardlinks):
        """Create a link at ``link_path`` pointing at ``source``.

        Raises OSError on failure, with a message explaining the Windows privilege case.
        Raising rather than reporting is deliberate: the caller has not deleted anything
        yet, and perform_on_marked turns the OSError into a recorded problem.
        """
        if use_hardlinks:
            os.link(str(source), str(link_path))
            return
        try:
            # target_is_directory is ignored on POSIX and required on Windows for a
            # directory symlink, which folder-mode scans produce.
            os.symlink(str(source), str(link_path), target_is_directory=source.is_dir())
        except OSError as e:
            if sys.platform == "win32" and e.errno in (errno.EPERM, errno.EACCES):
                raise OSError(
                    tr(
                        "Could not create a symbolic link at '{}'. On Windows, symbolic links "
                        "require either Developer Mode or the SeCreateSymbolicLinkPrivilege. "
                        "Consider using hardlinks instead."
                    ).format(str(link_path))
                ) from e
            raise

    def _create_file(self, path):
        # We add fs.Folder to fileclasses in case the file we're loading contains folder paths.
        return fs.get_file(path, self.fileclasses + [se.fs.Folder])

    def _get_file(self, str_path):
        path = Path(str_path)
        f = self._create_file(path)
        if f is None:
            return None
        try:
            f._read_all_info(attrnames=self.METADATA_TO_READ)
            return f
        except OSError:
            return None

    def _get_export_data(self):
        columns = [col for col in self.result_table._columns.ordered_columns if col.visible and col.name != "marked"]
        colnames = [col.display for col in columns]
        rows = []
        for group_id, group in enumerate(self.results.groups):
            for dupe in group:
                data = self.get_display_info(dupe, group)
                row = [fix_surrogate_encoding(data[col.name]) for col in columns]
                row.insert(0, group_id)
                rows.append(row)
        return colnames, rows

    def _results_changed(self):
        self.selected_dupes = [d for d in self.selected_dupes if self.results.get_group_of_duplicate(d) is not None]
        self.notify("results_changed")

    def _start_job(self, jobid, func, args=()):
        title = JOBID2TITLE[jobid]
        try:
            self.progress_window.run(jobid, title, func, args=args)
        except job.JobInProgressError:
            msg = tr(
                "A previous action is still hanging in there. You can't start a new one yet. Wait "
                "a few seconds, then try again."
            )
            self.view.show_message(msg)

    def _job_completed(self, jobid):
        if jobid == JobType.SCAN:
            self._results_changed()
            fs.filesdb.commit()
            from core.hash_cache import hashcachedb

            hashcachedb.commit()
            if not self.results.groups:
                self.view.show_message(tr("No duplicates found."))
            else:
                self.view.show_results_window()
        if jobid in {JobType.MOVE, JobType.DELETE}:
            self._results_changed()
        if jobid == JobType.LOAD:
            self._recreate_result_table()
            self._results_changed()
            self.view.show_results_window()
        if jobid in {JobType.COPY, JobType.MOVE, JobType.DELETE}:
            if self.results.problems:
                self.problem_dialog.refresh()
                self.view.show_problem_dialog()
            else:
                if jobid == JobType.COPY:
                    msg = tr("All marked files were copied successfully.")
                elif jobid == JobType.MOVE:
                    msg = tr("All marked files were moved successfully.")
                elif jobid == JobType.DELETE and self.deletion_options.direct:
                    msg = tr("All marked files were deleted successfully.")
                else:
                    msg = tr("All marked files were successfully sent to Trash.")
                self.view.show_message(msg)

    def _job_error(self, jobid, err):
        if jobid == JobType.LOAD:
            msg = tr("Could not load file: {}").format(err)
            self.view.show_message(msg)
            return False
        else:
            raise err

    @staticmethod
    def _remove_hardlink_dupes(files):
        seen_inodes = set()
        result = []
        for file in files:
            try:
                st = file.path.stat()
                inode = (st.st_dev, st.st_ino)
            except OSError:
                # The file was probably deleted or something
                continue
            if inode not in seen_inodes:
                seen_inodes.add(inode)
                result.append(file)
        return result

    def _select_dupes(self, dupes):
        if dupes == self.selected_dupes:
            return
        self.selected_dupes = dupes
        self.notify("dupes_selected")

    # --- Protected
    def _get_fileclasses(self):
        if self.app_mode == AppMode.PICTURE:
            return [pe.photo.PLAT_SPECIFIC_PHOTO_CLASS]
        elif self.app_mode == AppMode.MUSIC:
            return [me.fs.MusicFile]
        else:
            return [se.fs.File]

    def _prioritization_categories(self):
        if self.app_mode == AppMode.PICTURE:
            return pe.prioritize.all_categories()
        elif self.app_mode == AppMode.MUSIC:
            return me.prioritize.all_categories()
        else:
            return prioritize.all_categories()

    # --- Public
    def add_directory(self, d):
        """Adds folder ``d`` to :attr:`directories`.

        Shows an error message dialog if something bad happens.

        :param str d: path of folder to add
        """
        try:
            self.directories.add_path(Path(d))
            self.notify("directories_changed")
        except directories.AlreadyThereError:
            self.view.show_message(tr("'{}' already is in the list.").format(d))
        except directories.InvalidPathError:
            self.view.show_message(tr("'{}' does not exist.").format(d))

    def add_selected_to_ignore_list(self):
        """Adds :attr:`selected_dupes` to :attr:`ignore_list`."""
        dupes = self.without_ref(self.selected_dupes)
        if not dupes:
            self.view.show_message(MSG_NO_SELECTED_DUPES)
            return
        msg = tr("All selected %d matches are going to be ignored in all subsequent scans. Continue?")
        if not self.view.ask_yes_no(msg % len(dupes)):
            return
        for dupe in dupes:
            g = self.results.get_group_of_duplicate(dupe)
            for other in g:
                if other is not dupe:
                    self.ignore_list.ignore(str(other.path), str(dupe.path))
        self.remove_duplicates(dupes)
        self.ignore_list_dialog.refresh()

    def apply_filter(self, result_filter):
        """Apply a filter ``filter`` to the results so that it shows only dupe groups that match it.

        :param str filter: filter to apply
        """
        self.results.apply_filter(None)
        if self.options["escape_filter_regexp"]:
            result_filter = escape(result_filter, set("()[]\\.|+?^"))
            result_filter = escape(result_filter, "*", ".")
        self.results.apply_filter(result_filter)
        self._results_changed()

    def clean_empty_dirs(self, path):
        if self.options["clean_empty_dirs"]:
            while delete_if_empty(path, [".DS_Store"]):
                path = path.parent

    def clear_picture_cache(self):
        try:
            os.remove(self._get_picture_cache_path())
        except FileNotFoundError:
            pass  # we don't care

    def clear_hash_cache(self):
        fs.filesdb.clear()
        # hash_cache2.db is the cache the content-scan fast path actually reads
        # (core/scanner.py). Clearing only filesdb left every stale digest in place,
        # so "Clear Cache" did not do what it said.
        from core.hash_cache import hashcachedb

        hashcachedb.clear()

    def copy_or_move(self, dupe, copy: bool, destination: str, dest_type: DestType):
        source_path = dupe.path
        location_path = first(p for p in self.directories if p in dupe.path.parents)
        dest_path = Path(destination)
        if dest_type in {DestType.RELATIVE, DestType.ABSOLUTE}:
            # no filename, no windows drive letter
            source_base = source_path.relative_to(source_path.anchor).parent
            # location_path is None when the dupe *is* one of the scanned folders rather than
            # living inside one, which is the ordinary shape of a folder-mode result: add
            # /photos/2023 and /photos/2024, scan with --scan-type folders, and the dupes are
            # the added directories themselves. There is no meaningful path relative to
            # itself, so fall back to the absolute layout instead of dereferencing None.
            if dest_type == DestType.RELATIVE and location_path is not None:
                source_base = source_base.relative_to(location_path.relative_to(location_path.anchor))
            dest_path = dest_path.joinpath(source_base)
        if not dest_path.exists():
            dest_path.mkdir(parents=True)
        # Add filename to dest_path. For file move/copy, it's not required, but for folders, yes.
        dest_path = dest_path.joinpath(source_path.name)
        logging.debug("Copy/Move operation from '%s' to '%s'", source_path, dest_path)
        # Raises an EnvironmentError if there's a problem
        if copy:
            smart_copy(source_path, dest_path)
        else:
            smart_move(source_path, dest_path)
            self.clean_empty_dirs(source_path.parent)

    def copy_or_move_marked(self, copy):
        """Start an async move (or copy) job on marked duplicates.

        :param bool copy: If True, duplicates will be copied instead of moved
        """

        def do(j):
            def op(dupe):
                j.add_progress()
                self.copy_or_move(dupe, copy, destination, desttype)

            j.start_job(self.results.mark_count)
            self.results.perform_on_marked(op, not copy)

        if not self.results.mark_count:
            self.view.show_message(MSG_NO_MARKED_DUPES)
            return
        destination = self.view.select_dest_folder(
            tr("Select a directory to copy marked files to")
            if copy
            else tr("Select a directory to move marked files to")
        )
        if destination:
            desttype = self.options["copymove_dest_type"]
            jobid = JobType.COPY if copy else JobType.MOVE
            self._start_job(jobid, do)

    def delete_marked(self):
        """Start an async job to send marked duplicates to the trash."""
        if not self.results.mark_count:
            self.view.show_message(MSG_NO_MARKED_DUPES)
            return
        if self.results.has_marked_partial_matches():
            if not self.view.ask_yes_no(MSG_PARTIAL_HASH_WARNING):
                return
        if not self.deletion_options.show(self.results.mark_count):
            return
        args = [
            self.deletion_options.link_deleted,
            self.deletion_options.use_hardlinks,
            self.deletion_options.direct,
            self.deletion_options.use_clones,
        ]
        logging.debug("Starting deletion job with args %r", args)
        self._start_job(JobType.DELETE, self._do_delete, args=args)

    def save_scan_profile(self, name, settings=None):
        """Save the current folders, states and mode under *name*, replacing any profile of
        that name.

        *settings* is whatever the front end wants remembered alongside them, as a flat dict of
        scalars. Core stores it and hands it back unchanged; see :mod:`core.scan_profile` for
        why it does not try to interpret it.

        :rtype: core.scan_profile.ScanProfile
        """
        profile = ScanProfile.capture(name, self.directories, self.app_mode, settings)
        self.scan_profiles.set(profile)
        self.notify("scan_profiles_changed")
        return profile

    def apply_scan_profile(self, name):
        """Restore the folders, states and mode saved under *name*.

        Returns the profile's folders that no longer exist. They are skipped rather than
        refused -- with a drive unplugged, scanning what is present and being told what is not
        beats refusing outright -- but the caller must surface them. A scan that quietly covers
        four folders instead of five reports fewer duplicates, and that reads exactly like a
        clean result.

        Applying the profile's *settings* is the front end's job, since it is the front end
        that knows what they mean.

        :rtype: list of str
        """
        profile = self.scan_profiles.get(name)
        if profile is None:
            raise ScanProfileError(f"no scan profile named {name!r}")
        missing = profile.apply_folders(self.directories)
        self.app_mode = profile.app_mode
        self.notify("directories_changed")
        return missing

    def delete_scan_profile(self, name):
        """Forget the profile saved under *name*. Unknown names are ignored."""
        self.scan_profiles.remove(name)
        self.notify("scan_profiles_changed")

    def deletion_preview(self):
        """What :meth:`delete_marked` would actually do, without touching anything.

        Plans the files the user has marked, using the same predicate the deletion itself uses,
        so the preview cannot promise something the deletion then refuses. Cloning is assessed
        only when the user has asked for it, because the probe costs a filesystem test per
        candidate and answers a question nobody asked otherwise.

        :rtype: core.deletion_plan.DeletionPlan
        """
        # Imported here: core.deletion_plan imports check_deletable from this module.
        from core.deletion_plan import build_plan, default_clone_probe

        probe = default_clone_probe if self.deletion_options.use_clones else None
        return build_plan(self, clone_probe=probe)

    def export_to_xhtml(self):
        """Export current results to XHTML.

        The configuration of the :attr:`result_table` (columns order and visibility) is used to
        determine how the data is presented in the export. In other words, the exported table in
        the resulting XHTML will look just like the results table.
        """
        colnames, rows = self._get_export_data()
        export_path = export.export_to_xhtml(colnames, rows)
        desktop.open_path(export_path)

    def export_to_csv(self):
        """Export current results to CSV.

        The columns and their order in the resulting CSV file is determined in the same way as in
        :meth:`export_to_xhtml`.
        """
        dest_file = self.view.select_dest_file(tr("Select a destination for your exported CSV"), "csv")
        if dest_file:
            colnames, rows = self._get_export_data()
            try:
                export.export_to_csv(dest_file, colnames, rows)
            except OSError as e:
                self.view.show_message(tr("Couldn't write to file: {}").format(str(e)))

    def get_display_info(self, dupe, group, delta=False):
        def empty_data():
            return {c.name: "---" for c in self.result_table.COLUMNS[1:]}

        if (dupe is None) or (group is None):
            return empty_data()
        try:
            return dupe.get_display_info(group, delta)
        except Exception as e:
            logging.warning("Exception (type: %s) on GetDisplayInfo for %s: %s", type(e), str(dupe.path), str(e))
            return empty_data()

    def invoke_custom_command(self):
        """Calls command in ``CustomCommand`` pref with ``%d`` and ``%r`` placeholders replaced.

        Using the current selection, ``%d`` is replaced with the currently selected dupe and ``%r``
        is replaced with that dupe's ref file. If there's no selection, the command is not invoked.
        If the dupe is a ref, ``%d`` and ``%r`` will be the same.
        """
        cmd = self.view.get_default("CustomCommand")
        if not cmd:
            msg = tr("You have no custom command set up. Set it up in your preferences.")
            self.view.show_message(msg)
            return
        if not self.selected_dupes:
            return
        dupes = self.selected_dupes
        refs = [self.results.get_group_of_duplicate(dupe).ref for dupe in dupes]
        # Parse the template once into tokens so substitution happens per-token,
        # not on the full shell string — prevents filenames with metacharacters
        # (semicolons, ampersands, quotes, etc.) from being interpreted by a shell.
        try:
            cmd_tokens = shlex.split(cmd, posix=(sys.platform != "win32"))
        except ValueError as e:
            logging.warning("Could not parse CustomCommand %r: %s", cmd, e)
            self.view.show_message(tr("Custom command could not be parsed: {}").format(e))
            return
        for dupe, ref in zip(dupes, refs):
            argv = [token.replace("%d", str(dupe.path)).replace("%r", str(ref.path)) for token in cmd_tokens]
            p = subprocess.Popen(argv, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output = p.stdout.read()
            rc = p.wait()
            if rc != 0:
                logging.warning("Custom command %r exited with code %d: %s", argv, rc, output)
            else:
                logging.info("Custom command %r: %s", argv, output)

    def load(self):
        """Load directory selection and ignore list from files in appdata.

        This method is called during startup so that directory selection and ignore list, which
        is persistent data, is the same as when the last session was closed (when :meth:`save` was
        called).
        """
        self.directories.load_from_file(op.join(self.appdata, "last_directories.xml"))
        self.scan_profiles.load_from_file(op.join(self.appdata, "scan_profiles.xml"))
        self.notify("directories_changed")
        p = op.join(self.appdata, "ignore_list.xml")
        self.ignore_list.load_from_xml(p)
        self.ignore_list_dialog.refresh()
        p = op.join(self.appdata, "exclude_list.xml")
        self.exclude_list.load_from_xml(p)
        self.exclude_list_dialog.refresh()

    def load_directories(self, filepath):
        # Clear out previous entries. Note this used to call self.directories.__init__(),
        # which reset _exclude_list to None because that is the parameter's default, so
        # every scan after a directory load silently ignored the user's exclusions.
        self.directories.clear()
        self.directories.load_from_file(filepath)
        self.notify("directories_changed")

    def load_from(self, filename):
        """Start an async job to load results from ``filename``.

        :param str filename: path of the XML file (created with :meth:`save_as`) to load
        """

        def do(j):
            self.results.load_from_xml(filename, self._get_file, j)

        self._start_job(JobType.LOAD, do)

    def make_selected_reference(self):
        """Promote :attr:`selected_dupes` to reference position within their respective groups.

        Each selected dupe will become the :attr:`~core.engine.Group.ref` of its group. If there's
        more than one dupe selected for the same group, only the first (in the order currently shown
        in :attr:`result_table`) dupe will be promoted.
        """
        dupes = self.without_ref(self.selected_dupes)
        changed_groups = set()
        for dupe in dupes:
            g = self.results.get_group_of_duplicate(dupe)
            if g not in changed_groups and self.results.make_ref(dupe):
                changed_groups.add(g)
        # It's not always obvious to users what this action does, so to make it a bit clearer,
        # we change our selection to the ref of all changed groups. However, we also want to keep
        # the files that were ref before and weren't changed by the action. In effect, what this
        # does is that we keep our old selection, but remove all non-ref dupes from it.
        # If no group was changed, however, we don't touch the selection.
        if not self.result_table.power_marker:
            if changed_groups:
                self.selected_dupes = [
                    d for d in self.selected_dupes if self.results.get_group_of_duplicate(d).ref is d
                ]
            self.notify("results_changed")
        else:
            # If we're in "Dupes Only" mode (previously called Power Marker), things are a bit
            # different. The refs are not shown in the table, and if our operation is successful,
            # this means that there's no way to follow our dupe selection. Then, the best thing to
            # do is to keep our selection index-wise (different dupe selection, but same index
            # selection).
            self.notify("results_changed_but_keep_selection")

    def mark_all(self):
        """Set all dupes in the results as marked."""
        self.results.mark_all()
        self.notify("marking_changed")

    def mark_by_criterion(self, sort_key):
        """Promote the best-matching file to reference in each group, then mark all others.

        For every duplicate group the file that sorts lowest under ``sort_key`` is promoted to
        the reference position (i.e. it becomes the keeper).  All remaining files in the group
        are then marked.  Files inside a reference folder are never displaced and are never marked.

        :param sort_key: callable ``f(file) -> comparable`` — lower value = preferred keeper
        """
        for group in self.results.groups:
            group.prioritize(key_func=sort_key)
        self.results.refresh_required = True
        self.results.mark_none()
        self.results.mark_all()
        self.notify("marking_changed")
        self._results_changed()

    def mark_none(self):
        """Set all dupes in the results as unmarked."""
        self.results.mark_none()
        self.notify("marking_changed")

    def mark_invert(self):
        """Invert the marked state of all dupes in the results."""
        self.results.mark_invert()
        self.notify("marking_changed")

    def mark_dupe(self, dupe, marked):
        """Change marked status of ``dupe``.

        :param dupe: dupe to mark/unmark
        :type dupe: :class:`~core.fs.File`
        :param bool marked: True = mark, False = unmark
        """
        if marked:
            self.results.mark(dupe)
        else:
            self.results.unmark(dupe)
        self.notify("marking_changed")

    def open_selected(self):
        """Open :attr:`selected_dupes` with their associated application."""
        if len(self.selected_dupes) > 10 and not self.view.ask_yes_no(MSG_MANY_FILES_TO_OPEN):
            return
        for dupe in self.selected_dupes:
            desktop.open_path(dupe.path)

    def purge_ignore_list(self):
        """Remove files that don't exist from :attr:`ignore_list`."""
        self.ignore_list.filter(lambda f, s: op.exists(f) and op.exists(s))
        self.ignore_list_dialog.refresh()

    def remove_directories(self, indexes):
        """Remove root directories at ``indexes`` from :attr:`directories`.

        :param indexes: Indexes of the directories to remove.
        :type indexes: list of int
        """
        try:
            indexes = sorted(indexes, reverse=True)
            for index in indexes:
                del self.directories[index]
            self.notify("directories_changed")
        except IndexError:
            pass

    def remove_duplicates(self, duplicates):
        """Remove ``duplicates`` from :attr:`results`.

        Calls :meth:`~core.results.Results.remove_duplicates` and send appropriate notifications.

        :param duplicates: duplicates to remove.
        :type duplicates: list of :class:`~core.fs.File`
        """
        self.results.remove_duplicates(self.without_ref(duplicates))
        self.notify("results_changed_but_keep_selection")

    def remove_marked(self):
        """Removed marked duplicates from the results (without touching the files themselves)."""
        if not self.results.mark_count:
            self.view.show_message(MSG_NO_MARKED_DUPES)
            return
        msg = tr("You are about to remove %d files from results. Continue?")
        if not self.view.ask_yes_no(msg % self.results.mark_count):
            return
        self.results.perform_on_marked(lambda x: None, True)
        self._results_changed()

    def remove_selected(self):
        """Removed :attr:`selected_dupes` from the results (without touching the files themselves)."""
        dupes = self.without_ref(self.selected_dupes)
        if not dupes:
            self.view.show_message(MSG_NO_SELECTED_DUPES)
            return
        msg = tr("You are about to remove %d files from results. Continue?")
        if not self.view.ask_yes_no(msg % len(dupes)):
            return
        self.remove_duplicates(dupes)

    def rename_selected(self, newname):
        """Renames the selected dupes's file to ``newname``.

        If there's more than one selected dupes, the first one is used.

        :param str newname: The filename to rename the dupe's file to.
        """
        try:
            d = self.selected_dupes[0]
            d.rename(newname)
            return True
        except (IndexError, fs.FSError) as e:
            logging.warning("dupeGuru Warning: %s" % str(e))
        return False

    def reprioritize_groups(self, sort_key):
        """Sort dupes in each group (in :attr:`results`) according to ``sort_key``.

        Called by the re-prioritize dialog. Calls :meth:`~core.engine.Group.prioritize` and, once
        the sorting is done, show a message that confirms the action.

        :param sort_key: The key being sent to :meth:`~core.engine.Group.prioritize`
        :type sort_key: f(dupe)
        """
        count = 0
        for group in self.results.groups:
            if group.prioritize(key_func=sort_key):
                count += 1
        if count:
            self.results.refresh_required = True
        self._results_changed()
        msg = tr("{} duplicate groups were changed by the re-prioritization.").format(count)
        self.view.show_message(msg)

    def reveal_selected(self):
        if self.selected_dupes:
            desktop.reveal_path(self.selected_dupes[0].path)

    def save(self):
        if not op.exists(self.appdata):
            os.makedirs(self.appdata)
        self.directories.save_to_file(op.join(self.appdata, "last_directories.xml"))
        self.scan_profiles.save_to_file(op.join(self.appdata, "scan_profiles.xml"))
        p = op.join(self.appdata, "ignore_list.xml")
        self.ignore_list.save_to_xml(p)
        p = op.join(self.appdata, "exclude_list.xml")
        self.exclude_list.save_to_xml(p)
        self.notify("save_session")

    def close(self):
        if self.picture_match_cache is not None:
            self.picture_match_cache.close()
            self.picture_match_cache = None
        fs.filesdb.close()
        from core.hash_cache import hashcachedb

        hashcachedb.close()

    def save_as(self, filename):
        """Save results in ``filename``.

        :param str filename: path of the file to save results (as XML) to.
        """
        try:
            self.results.save_to_xml(filename)
        except OSError as e:
            self.view.show_message(tr("Couldn't write to file: {}").format(str(e)))

    def save_directories_as(self, filename):
        """Save directories in ``filename``.

        :param str filename: path of the file to save directories (as XML) to.
        """
        try:
            self.directories.save_to_file(filename)
        except OSError as e:
            self.view.show_message(tr("Couldn't write to file: {}").format(str(e)))

    def start_scanning(self, profile_scan=False):
        """Starts an async job to scan for duplicates.

        Scans folders selected in :attr:`directories` and put the results in :attr:`results`
        """
        scanner = self.SCANNER_CLASS()
        fs.filesdb.ignore_mtime = self.options["rehash_ignore_mtime"] is True
        fs.filesdb.purge_if_stale()
        from core.hash_cache import hashcachedb

        hashcachedb.purge_if_stale()
        if not self.directories.has_any_file():
            self.view.show_message(tr("The selected directories contain no scannable file."))
            return
        # Warn when the selected folders span multiple storage devices.  Scanning a drive
        # that contains backups alongside the originals risks marking originals for deletion
        # if reference folders are not set correctly.
        if len(self.directories) > 1 and self._dirs_span_multiple_devices(self.directories):
            msg = tr(
                "The selected folders are on different drives or volumes. Scanning drives that "
                "contain both originals and backups together risks marking original files for "
                "deletion if your reference folders are not configured correctly.\n\n"
                "Continue with the scan?"
            )
            if not self.view.ask_yes_no(msg):
                return
        # Send relevant options down to the scanner instance
        for k, v in self.options.items():
            if hasattr(scanner, k):
                setattr(scanner, k, v)
        if self.app_mode == AppMode.PICTURE:
            scanner.cache_path = self._get_picture_cache_path()
            scanner.match_cache = self.picture_match_cache
        self.results.groups = []
        self._recreate_result_table()
        self._results_changed()

        def do(j):
            if profile_scan:
                pr = cProfile.Profile()
                pr.enable()
            j.set_progress(0, tr("Collecting files to scan"))
            if scanner.scan_type == ScanType.FOLDERS:
                files = list(self.directories.get_folders(folderclass=se.fs.Folder, j=j))
            else:
                files = list(self.directories.get_files(fileclasses=self.fileclasses, j=j))
            if self.options["ignore_hardlink_matches"]:
                files = self._remove_hardlink_dupes(files)
            logging.info("Scanning %d files" % len(files))
            self.results.groups = scanner.get_dupe_groups(files, self.ignore_list, j)
            self.discarded_file_count = scanner.discarded_file_count
            self.discarded_partial_count = getattr(scanner, "discarded_partial_count", 0)
            self.verified_partial_count = getattr(scanner, "verified_partial_count", 0)
            if profile_scan:
                pr.disable()
                pr.dump_stats(op.join(self.appdata, f"{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.profile"))

        self._start_job(JobType.SCAN, do)

    def toggle_selected_mark_state(self):
        selected = self.without_ref(self.selected_dupes)
        if not selected:
            return
        if allsame(self.results.is_marked(d) for d in selected):
            markfunc = self.results.mark_toggle
        else:
            markfunc = self.results.mark
        for dupe in selected:
            markfunc(dupe)
        self.notify("marking_changed")

    def without_ref(self, dupes):
        """Returns ``dupes`` with all reference elements removed."""
        return [dupe for dupe in dupes if self.results.get_group_of_duplicate(dupe).ref is not dupe]

    def get_default(self, key, fallback_value=None):
        result = nonone(self.view.get_default(key), fallback_value)
        if fallback_value is not None and not isinstance(result, type(fallback_value)):
            # we don't want to end up with garbage values from the prefs
            try:
                result = type(fallback_value)(result)
            except Exception:
                result = fallback_value
        return result

    def set_default(self, key, value):
        self.view.set_default(key, value)

    # --- Properties
    @property
    def stat_line(self):
        result = self.results.stat_line
        if self.discarded_file_count:
            result = tr("%s (%d discarded)") % (result, self.discarded_file_count)
        return result

    @property
    def fileclasses(self):
        return self._get_fileclasses()

    @property
    def SCANNER_CLASS(self):
        if self.app_mode == AppMode.PICTURE:
            return pe.scanner.ScannerPE
        elif self.app_mode == AppMode.MUSIC:
            return me.scanner.ScannerME
        else:
            return se.scanner.ScannerSE

    @property
    def METADATA_TO_READ(self):
        if self.app_mode == AppMode.PICTURE:
            return ["size", "mtime", "dimensions", "exif_timestamp"]
        elif self.app_mode == AppMode.MUSIC:
            return [
                "size",
                "mtime",
                "duration",
                "bitrate",
                "samplerate",
                "title",
                "artist",
                "album",
                "genre",
                "year",
                "track",
                "comment",
            ]
        else:
            return ["size", "mtime"]
