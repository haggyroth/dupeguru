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

The on-disk format follows from the first rule, and used to contradict it (issue #198). Records
are **appended**, one self-contained JSON object per line. Writing per file was always the
intent -- so that a crash costs one entry -- but the file was rewritten in full each time, from
a single XML document that ``load()`` discarded entirely if any part of it failed to parse. So
each write truncated the whole history and rebuilt it, and a crash mid-write lost everything,
once per deleted file. It also made deleting *n* files cost O(n^2): 4,000 files spent 34.9 s in
the log alone.

One line per record fixes both, because both are properties of the format rather than of the
code around it. Appending is the natural write, a truncated tail costs the partial last line,
and a damaged line is skipped while the lines around it still load. A destination learned after
the record was written -- which is every trashed file, since the trash reports where it put
things -- is appended as an amendment rather than paid for with a rewrite.
"""

import json
import logging
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


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
        """Add a file to *run* and append it to the log immediately.

        Written per file rather than per run, and by the caller *before* the file is deleted,
        so a crash mid-run costs at most the entry for the file being deleted at that moment.

        That was the intent before this was appended rather than rewritten, but it was not what
        happened: ``save()`` opened the file truncating and wrote every run from scratch, so a
        crash during any single write destroyed the whole history -- and it ran once per deleted
        file, so deleting more files opened more windows to lose everything in. Appending one
        line delivers the guarantee the paragraph above always claimed.
        """
        run.records.append(record)
        self._append(_record_line(run, record))

    def record_destination(self, run: DeletionRun, record: DeletionRecord) -> None:
        """Note where a trashed file went, after the trash has said.

        The record is written before the deletion, when the destination cannot be known yet --
        it is what ``core.trash`` reads back afterwards. Rather than rewriting the file to fill
        it in, this appends an amendment naming the record it updates. Append-only is what keeps
        the cost linear and the crash window one line wide; an amendment that never arrives
        leaves a record whose destination is empty, which already means "no restore for this
        one" everywhere else.
        """
        self._append(_amendment_line(run, record))

    def discard_if_empty(self, run: DeletionRun) -> None:
        """Drop a run that recorded nothing, so cancelled deletions leave no empty entry.

        No longer touches the file. A run is only ever on disk through its records, so one that
        recorded nothing never wrote anything to take back.
        """
        if not run.records and run in self.runs:
            self.runs.remove(run)

    # --- Persistence

    def _append(self, line: str) -> None:
        """Add one line to the log, creating it if needed.

        Opened per line rather than held open for the run. A handle kept across a deletion has
        to survive cancellation, exceptions and a front end that never finishes the run, and
        getting that wrong loses records; an open-write-close costs a few microseconds against
        a deletion that touches the disk anyway.
        """
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError:
            # Never let logging failure break a deletion. The user asked to delete files, not
            # to maintain a log, and refusing the deletion because the log could not be written
            # would be a worse outcome than losing the undo.
            logging.warning("Could not append to the deletion log at %s", self.path, exc_info=True)

    def save(self) -> None:
        """Rewrite the whole file from memory.

        Only for compaction and for migrating an old XML log -- both once-per-load operations.
        Deliberately *not* used per record: doing that is what made deleting n files cost
        O(n^2), and what put the entire history at risk on every single write.

        Written to a temporary file and moved into place, so an interrupted compaction leaves
        the previous log intact rather than a half-written one.
        """
        if not self.path:
            return
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fp:
                for run in sorted(self.runs, key=lambda r: r.started_at):
                    for record in run.records:
                        fp.write(_record_line(run, record) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            logging.warning("Could not write the deletion log to %s", self.path, exc_info=True)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def load(self) -> None:
        """Read the log, keeping every record that still parses.

        Damage costs the damaged lines and nothing else. It used to cost everything: the reader
        parsed the file as one XML document and ``except Exception: return`` left the log empty,
        so a crash mid-write, one invalid byte, or a single stray ``&`` anywhere in the file
        discarded every run in it -- silently, which is the part that made it dangerous.

        An old XML log is read and rewritten in the new format, so upgrading keeps the undo
        history rather than quietly starting over.
        """
        self.runs = []
        if not self.path:
            return

        text = self._read_text(self.path)
        if text is None:
            # Nothing at the configured path. An older version wrote XML beside it under the
            # same stem; read that once and carry it forward.
            legacy = os.path.splitext(self.path)[0] + ".xml"
            legacy_text = self._read_text(legacy) if legacy != self.path else None
            if legacy_text is None:
                return
            self.runs = _parse_legacy_xml(legacy_text)
            self._trim()
            self.save()
            return

        if text.lstrip().startswith("<"):
            # The file at our own path is still in the old format.
            self.runs = _parse_legacy_xml(text)
            self._trim()
            self.save()
            return

        self.runs, damaged = _parse_lines(text)
        if damaged:
            logging.warning("Skipped %d damaged line(s) in the deletion log at %s", damaged, self.path)
        if self._trim() or damaged:
            # Compact on read rather than on write: this is the one moment the in-memory log is
            # known to hold everything on disk, so it is the only safe moment to rewrite it.
            # Trimming during a deletion would rewrite from whatever happened to be loaded.
            self.save()

    @staticmethod
    def _read_text(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fp:
                return fp.read()
        except OSError:
            return None

    def _trim(self) -> bool:
        """Keep only the newest MAX_RUNS runs. True if anything was dropped."""
        if len(self.runs) <= self.MAX_RUNS:
            return False
        self.runs.sort(key=lambda run: run.started_at)
        del self.runs[: len(self.runs) - self.MAX_RUNS]
        return True


def _record_line(run: DeletionRun, record: DeletionRecord) -> str:
    """One record as a self-contained JSON object.

    Each line repeats its run's identity rather than referring back to a header line. That is
    about sixty redundant bytes per record, and it buys the property the whole format exists
    for: a damaged line costs exactly that record. A header carrying the run would orphan
    every record under it.
    """
    return json.dumps(
        {
            "run": run.run_id,
            "started_at": run.started_at.isoformat(),
            "run_permanent": run.permanent,
            "path": record.original_path,
            "size": record.size,
            "digest": record.digest,
            "destination": record.destination,
            "reference": record.reference_path,
            "permanent": record.permanent,
        },
        ensure_ascii=False,
    )


def _amendment_line(run: DeletionRun, record: DeletionRecord) -> str:
    """A destination learned after the record was written."""
    return json.dumps(
        {"run": run.run_id, "amend": record.original_path, "destination": record.destination},
        ensure_ascii=False,
    )


def _parse_lines(text: str):
    """Read JSONL into runs, skipping what will not parse. Returns (runs, damaged_count)."""
    runs: dict = {}
    order: list = []
    damaged = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError("not an object")
            run_id = entry["run"]
        except (ValueError, TypeError, KeyError):
            # A truncated final line -- the normal shape of a crash -- lands here, as does any
            # single corrupted record. Both cost one line.
            damaged += 1
            continue

        if "amend" in entry:
            run = runs.get(run_id)
            if run is not None:
                for record in run.records:
                    if record.original_path == entry["amend"]:
                        record.destination = entry.get("destination", "") or ""
                        break
            continue

        if run_id not in runs:
            try:
                started_at = datetime.fromisoformat(entry.get("started_at", ""))
            except (ValueError, TypeError):
                started_at = datetime.now()
            runs[run_id] = DeletionRun(
                run_id=run_id,
                started_at=started_at,
                permanent=bool(entry.get("run_permanent", False)),
            )
            order.append(run_id)
        try:
            size = int(entry.get("size", 0))
        except (ValueError, TypeError):
            size = 0
        runs[run_id].records.append(
            DeletionRecord(
                original_path=str(entry.get("path", "")),
                size=size,
                digest=str(entry.get("digest", "") or ""),
                destination=str(entry.get("destination", "") or ""),
                reference_path=str(entry.get("reference", "") or ""),
                permanent=bool(entry.get("permanent", False)),
            )
        )
    return [runs[run_id] for run_id in order if runs[run_id].records], damaged


def _parse_legacy_xml(text: str) -> list:
    """Read a log written before this was line-based.

    Still all-or-nothing, because XML is: a damaged document cannot be partially parsed. That
    is the reason for the format change rather than an argument for keeping this shape, and it
    only ever runs once, on the upgrade.
    """
    runs = []
    try:
        root = ET.fromstring(text)
    except Exception:
        logging.warning("The previous deletion log could not be read and was not carried over")
        return runs
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
            runs.append(run)
    return runs


def default_log_path(appdata) -> str:
    """Where the log lives, beside the other application data.

    ``.jsonl`` rather than ``.xml``: the format changed, and a new name means an older build
    reading the directory finds no log rather than a file it cannot parse. ``load()`` picks up
    the ``.xml`` beside it once, on the first run after upgrading.
    """
    return os.path.join(appdata, "deletion_log.jsonl")
