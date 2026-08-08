# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A record of what each deletion removed, and a way to put it back (issue #125).

Everything else in the application prevents a *wrong* deletion: ``check_deletable``
re-validates immediately before removing, the preview shows what would happen, cloning avoids
destroying anything at all. None of that helps with a deletion that was correct at the time
and regretted afterwards.

Two rules shape this module, and both come from the same place -- a safety feature that lies
is worse than none, because people rely on it.

**The record is written before the deletion, not after.** A manifest that can be lost leaves
the user believing they have an undo they do not have. Recording first means the worst case is
an entry for a file that was never removed, which restore detects and reports, rather than a
removed file with no entry.

**Restore verifies rather than assumes.** The trashed copy may be gone, the original path may
be occupied by something newer, or the file may have been put back by hand already. Restoring
blindly would create exactly the data loss this exists to undo, so every one of those is
checked and refused rather than guessed at.
"""

import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from hscommon.util import FileOrPath


class RestoreStatus:
    """Outcome of trying to put one file back."""

    RESTORED = "restored"  # the file is at its original path again
    ALREADY_THERE = "already_there"  # nothing to do; something is already at the original path
    NO_BACKUP = "no_backup"  # the trashed copy is gone (trash emptied, or restored by hand)
    OCCUPIED = "occupied"  # a *different* file now holds the original path
    PERMANENT = "permanent"  # deleted outright; there was never anything to restore
    FAILED = "failed"  # the move itself failed


#: Why a file could not be restored, in words. Kept here so every front end says the same
#: thing about the same status.
RESTORE_STATUS_REASON = {
    RestoreStatus.ALREADY_THERE: "a file is already at the original path",
    RestoreStatus.NO_BACKUP: "the trashed copy is no longer there",
    RestoreStatus.OCCUPIED: "a different file now occupies the original path",
    RestoreStatus.PERMANENT: "this file was deleted permanently, not sent to the trash",
    RestoreStatus.FAILED: "the file could not be moved back",
}


class DeletionRecord:
    """One deleted file."""

    def __init__(self, original_path, size=0, digest="", destination="", reference_path="", permanent=False):
        self.original_path = str(original_path)
        self.size = int(size)
        #: Hex digest recorded at scan time, when there was one. Used to tell "the same file is
        #: back" from "something else is here now".
        self.digest = digest
        #: Where the trashed copy went, or "" when that could not be captured.
        self.destination = destination
        #: The file this one duplicated, which is the context that makes the record readable
        #: months later.
        self.reference_path = reference_path
        self.permanent = bool(permanent)

    def __repr__(self):
        return f"<DeletionRecord {self.original_path!r}>"

    @property
    def restorable(self) -> bool:
        """Whether a restore could even be attempted.

        False for permanent deletions and for trashed files whose destination was never
        captured. Front ends use this to avoid offering a button that cannot work.
        """
        return not self.permanent and bool(self.destination)


class DeletionRun:
    """One deletion operation: everything removed in a single press of Delete."""

    def __init__(self, run_id="", started_at=None, permanent=False, records=None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.started_at = started_at or datetime.now()
        #: Whether the run bypassed the trash. Recorded per run *and* per record: the run-level
        #: flag is what the user chose, and a record can still be permanent within a
        #: trash run if the trash was unavailable for that file.
        self.permanent = bool(permanent)
        self.records = list(records or [])

    def __repr__(self):
        return f"<DeletionRun {self.run_id} {len(self.records)} file(s)>"

    def __len__(self):
        return len(self.records)

    @property
    def total_bytes(self) -> int:
        return sum(record.size for record in self.records)

    @property
    def restorable_count(self) -> int:
        return sum(1 for record in self.records if record.restorable)


def restore_record(record: DeletionRecord) -> tuple:
    """Put one file back. Returns ``(status, message)`` and never raises.

    Refuses in every case where putting the file back could destroy something. The caller gets
    a status it can report rather than an exception it has to interpret.
    """
    if record.permanent:
        return RestoreStatus.PERMANENT, RESTORE_STATUS_REASON[RestoreStatus.PERMANENT]
    if not record.destination:
        return RestoreStatus.NO_BACKUP, "where this file went was not recorded, so it cannot be found"

    original = Path(record.original_path)
    backup = Path(record.destination)

    # The user's own path is checked first, and deliberately so. Checking the trashed copy
    # first answers "the trashed copy is no longer there" for a file that has *already been
    # restored* -- true, and read by anyone as "your file is gone", which is the opposite of
    # what happened. What matters is the state of the original path.
    if original.exists():
        # Distinguish "already restored" from "something else is here now". Overwriting the
        # second case with an older copy is precisely the data loss this feature exists to
        # prevent, so anything short of a positive match refuses.
        if backup.exists() and _same_file(original, backup):
            return RestoreStatus.ALREADY_THERE, RESTORE_STATUS_REASON[RestoreStatus.ALREADY_THERE]
        if not backup.exists() and record.digest and _digest_of(original) == record.digest:
            # The trashed copy is gone and the recorded file is back where it belongs, so it
            # was restored -- by us, or by hand. This is what the digest was recorded for.
            return RestoreStatus.ALREADY_THERE, RESTORE_STATUS_REASON[RestoreStatus.ALREADY_THERE]
        return RestoreStatus.OCCUPIED, RESTORE_STATUS_REASON[RestoreStatus.OCCUPIED]

    if not backup.exists():
        return RestoreStatus.NO_BACKUP, RESTORE_STATUS_REASON[RestoreStatus.NO_BACKUP]

    try:
        original.parent.mkdir(parents=True, exist_ok=True)
        # move rather than rename: the trash is often on a different filesystem from where the
        # file came from, and rename cannot cross one.
        shutil.move(str(backup), str(original))
    except (OSError, shutil.Error) as e:
        return RestoreStatus.FAILED, f"{RESTORE_STATUS_REASON[RestoreStatus.FAILED]}: {e}"
    return RestoreStatus.RESTORED, ""


def _digest_of(path: Path) -> str:
    """The file's digest, in the same form the record stores, or "" if unreadable.

    Uses core.fs's hasher so a digest computed here is comparable with one recorded at scan
    time. A different algorithm would silently never match, turning every already-restored
    file into a refusal.
    """
    from core.fs import hasher

    try:
        file_hash = hasher()
        with path.open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                file_hash.update(chunk)
        return file_hash.digest().hex()
    except OSError:
        return ""


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths hold the same content, cheaply.

    Size first, then a full comparison only when the sizes agree. Used to tell an
    already-restored file from a different one that happens to sit at the same path.
    """
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                chunk_a, chunk_b = fa.read(65536), fb.read(65536)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


class DeletionLog:
    """Every recorded deletion run, newest first."""

    #: Runs kept on disk. Old enough runs are useless -- the trash they refer to has long been
    #: emptied -- and an unbounded file would grow forever on a machine that dedupes often.
    MAX_RUNS = 50

    def __init__(self, path=None):
        self.runs = []
        #: Where to persist. Set by the application; a log with no path still works in memory,
        #: which is what keeps this testable and what the CLI uses when not asked to record.
        self.path = str(path) if path else ""

    def __len__(self):
        return len(self.runs)

    def __iter__(self):
        """Newest run first, which is the order a user wants to see them in."""
        return iter(sorted(self.runs, key=lambda run: run.started_at, reverse=True))

    def get(self, run_id):
        for run in self.runs:
            if run.run_id == run_id:
                return run
        return None

    def start_run(self, permanent: bool) -> DeletionRun:
        """Open a run. Not persisted until something is recorded in it."""
        run = DeletionRun(permanent=permanent)
        self.runs.append(run)
        del self.runs[: max(0, len(self.runs) - self.MAX_RUNS)]
        return run

    def record(self, run: DeletionRun, record: DeletionRecord) -> None:
        """Add a file to *run* and persist immediately.

        Written per file rather than per run, and by the caller *before* the file is deleted.
        A crash mid-run then costs at most the entry for the file being deleted at that moment,
        instead of the whole run's worth of records.
        """
        run.records.append(record)
        self.save()

    def discard_if_empty(self, run: DeletionRun) -> None:
        """Drop a run that recorded nothing, so cancelled deletions leave no empty entry."""
        if not run.records and run in self.runs:
            self.runs.remove(run)
            self.save()

    # --- Persistence

    def save(self) -> None:
        if not self.path:
            return
        try:
            with FileOrPath(self.path, "wb") as fp:
                root = ET.Element("deletion_log")
                for run in sorted(self.runs, key=lambda r: r.started_at):
                    run_node = ET.SubElement(root, "run")
                    run_node.set("id", run.run_id)
                    run_node.set("started_at", run.started_at.isoformat())
                    run_node.set("permanent", str(run.permanent))
                    for record in run.records:
                        node = ET.SubElement(run_node, "file")
                        node.set("path", record.original_path)
                        node.set("size", str(record.size))
                        node.set("permanent", str(record.permanent))
                        if record.digest:
                            node.set("digest", record.digest)
                        if record.destination:
                            node.set("destination", record.destination)
                        if record.reference_path:
                            node.set("reference", record.reference_path)
                ET.ElementTree(root).write(fp, encoding="utf-8")
        except OSError:
            # Never let logging failure break a deletion. The user asked to delete files, not
            # to maintain a log, and refusing the deletion because the log could not be written
            # would be a worse outcome than losing the undo.
            import logging

            logging.warning("Could not write the deletion log to %s", self.path, exc_info=True)

    def load(self) -> None:
        """Read the log, leaving it empty if the file is missing or damaged."""
        self.runs = []
        if not self.path:
            return
        try:
            root = ET.parse(self.path).getroot()
        except Exception:
            return
        for run_node in root.iter("run"):
            try:
                started_at = datetime.fromisoformat(run_node.get("started_at", ""))
            except ValueError:
                started_at = datetime.now()
            run = DeletionRun(
                run_id=run_node.get("id", ""),
                started_at=started_at,
                permanent=run_node.get("permanent") == "True",
            )
            for node in run_node.iter("file"):
                path = node.get("path")
                if not path:
                    continue
                try:
                    size = int(node.get("size", "0"))
                except ValueError:
                    size = 0
                run.records.append(
                    DeletionRecord(
                        original_path=path,
                        size=size,
                        digest=node.get("digest", ""),
                        destination=node.get("destination", ""),
                        reference_path=node.get("reference", ""),
                        permanent=node.get("permanent") == "True",
                    )
                )
            if run.records:
                self.runs.append(run)


def default_log_path(appdata) -> str:
    """Where the log lives, beside the other application data."""
    return os.path.join(appdata, "deletion_log.xml")
