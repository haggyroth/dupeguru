# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The log is appended to, and damage costs only what was damaged (issue #198).

Two defects, one cause. ``record()`` called ``save()``, and ``save()`` rewrote every run from
scratch, so deleting *n* files wrote O(n^2) and 4,000 files spent 34.9 s on nothing but log
maintenance -- about 28 minutes for the 23,857-file cluster that #180 made scannable, and twice
that through the trash, which saved a second time per file.

The same rewrite put the whole history at risk. ``save()`` opened the path truncating, so a
crash during any single write left a truncated document, and ``load()`` parsed the file as one
XML tree with ``except Exception: return`` -- discarding *every* run in it, silently. Measured
on a log of 3 runs and 12 records: a truncation at 80% recovered nothing, and so did one stray
``&`` anywhere in the file.

That inverted the reason the per-file write existed. It was chosen so a crash would cost one
entry; it actually risked everything, once per deleted file, so deleting more files opened more
windows to lose all of it.

The fix is one record per line, appended. Both properties fall out of the format: appending is
the natural write, and a damaged line can be skipped while every line around it still loads.
These tests pin the two properties rather than the format, except where the format is the
argument -- the self-contained line, and the amendment.
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from core.deletion_log import DeletionLog, DeletionRecord


def build(path, runs=3, per_run=4):
    """A log with several runs on disk, written the way a deletion writes it."""
    log = DeletionLog(path)
    for r in range(runs):
        run = log.start_run(permanent=False)
        # Distinct timestamps, so ordering is well defined rather than incidental.
        run.started_at = datetime(2026, 1, 1) + timedelta(hours=r)
        for i in range(per_run):
            log.record(
                run,
                DeletionRecord(f"/d/r{r}f{i}.bin", size=10, destination=f"/trash/r{r}f{i}.bin"),
            )
    return log


def reloaded(path):
    log = DeletionLog(path)
    log.load()
    return log


def counts(log):
    return len(log.runs), sum(len(run) for run in log.runs)


class TestItIsAppendedNotRewritten:
    def test_recording_never_rewrites_the_whole_log(self, tmp_path):
        """The invariant behind the cost. save() rewrites everything, so it must not run here."""
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        rewrites = []
        log.save = lambda *a, **k: rewrites.append(1)  # type: ignore[method-assign]

        run = log.start_run(permanent=False)
        for i in range(20):
            log.record(run, DeletionRecord(f"/d/f{i}.bin", destination=f"/trash/f{i}.bin"))

        assert rewrites == [], "recording a file rewrote the entire log"

    def test_the_bytes_written_grow_linearly(self, tmp_path):
        """Four times the files, about four times the writing -- not sixteen.

        Counted rather than timed: a wall-clock assertion is the flaky way to say this, and the
        quantity that actually changed is how much gets written.

        Every write is counted, whichever method makes it, so an implementation that goes back
        to rewriting fails here with a number rather than by making the counter unreachable.
        """
        written = {}
        for n in (25, 50, 100):
            path = tmp_path / f"log{n}.jsonl"
            log = DeletionLog(path)
            total = [0]
            real_append, real_save = log._append, log.save

            def counting_append(line, _total=total, _real=real_append):
                _total[0] += len(line)
                _real(line)

            def counting_save(_total=total, _real=real_save, _log=log):
                _total[0] += sum(len(rec.original_path) + 120 for run in _log.runs for rec in run.records)
                _real()

            log._append = counting_append  # type: ignore[method-assign]
            log.save = counting_save  # type: ignore[method-assign]
            run = log.start_run(permanent=False)
            for i in range(n):
                log.record(run, DeletionRecord(f"/d/f{i}.bin", destination=f"/trash/f{i}.bin"))
            written[n] = total[0]

        assert min(written.values()) > 0, "nothing was written at all"
        per_file = {n: total / n for n, total in written.items()}
        spread = max(per_file.values()) / min(per_file.values())
        assert spread < 1.3, f"cost per file is not flat: {per_file}"
        # The rewriting form wrote n(n+1)/2 records to record n: 5,050 against 325 here.
        assert written[100] < 6 * written[25], f"growth looks quadratic: {written}"

    def test_a_run_that_records_nothing_writes_nothing(self, tmp_path):
        # discard_if_empty no longer touches the file, because an empty run never reached it.
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        log.discard_if_empty(run)
        assert not path.exists()
        assert len(log) == 0


class TestDamageCostsOnlyWhatWasDamaged:
    """The table from the issue. Every one of these used to recover nothing at all."""

    def test_an_intact_log_reads_back_whole(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path)
        assert counts(reloaded(path)) == (3, 12)

    def test_a_truncated_log_keeps_everything_before_the_cut(self, tmp_path):
        """The shape of a crash, and the case that matters most.

        Appending makes a partial final line the *normal* interrupted state rather than an
        exotic one, so this has to hold rather than merely not raise.
        """
        path = tmp_path / "log.jsonl"
        build(path)
        raw = path.read_bytes()
        path.write_bytes(raw[: int(len(raw) * 0.8)])

        runs, records = counts(reloaded(path))
        assert runs == 3, "complete earlier runs were lost to a truncation in the last one"
        assert 8 <= records < 12, f"expected most records to survive the cut, got {records}"

    def test_one_unparseable_line_costs_one_record(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[5] = "{this is not json"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        runs, records = counts(reloaded(path))
        assert (runs, records) == (3, 11), "a damaged line took more than itself"

    def test_a_line_that_is_not_an_object_is_skipped(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write('"a bare string"\n[1, 2, 3]\n')
        assert counts(reloaded(path)) == (3, 12)

    def test_a_stray_ampersand_is_not_special(self, tmp_path):
        # It was, in XML: one unescaped '&' anywhere discarded the entire file.
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/d/a&b.bin", destination="/trash/a&b.bin"))
        assert reloaded(path).runs[0].records[0].original_path == "/d/a&b.bin"

    def test_blank_lines_are_not_damage(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path)
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("\n", "\n\n"), encoding="utf-8")
        assert counts(reloaded(path)) == (3, 12)

    def test_a_wholly_unreadable_file_still_leaves_an_empty_log(self, tmp_path):
        # The pre-existing guarantee: never raise at the caller.
        path = tmp_path / "log.jsonl"
        path.write_bytes(b"\x00\x01\x02 not a log at all\n")
        log = reloaded(path)
        assert len(log) == 0

    def test_a_missing_log_is_harmless(self, tmp_path):
        assert len(reloaded(tmp_path / "never-written.jsonl")) == 0


class TestTheLineIsSelfContained:
    """Why each line repeats its run rather than pointing at a header."""

    def test_every_line_carries_its_own_run(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path, runs=2, per_run=2)
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            assert entry["run"], "a line that cannot name its run is orphaned if a header is lost"
            assert entry["started_at"]

    def test_losing_the_first_line_of_a_run_keeps_the_rest(self, tmp_path):
        """The property the redundancy buys. A run header would have orphaned these."""
        path = tmp_path / "log.jsonl"
        build(path, runs=1, per_run=5)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

        runs, records = counts(reloaded(path))
        assert (runs, records) == (1, 4), "the run did not survive losing its first record"


class TestTheDestinationAmendment:
    """The destination is learned after the record is written, and must not cost a rewrite."""

    def test_an_amended_destination_survives_a_reload(self, tmp_path):
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        record = DeletionRecord("/d/a.bin")
        log.record(run, record)  # written before the deletion; no destination yet
        assert reloaded(path).runs[0].records[0].destination == ""

        record.destination = "/trash/a.bin"
        log.record_destination(run, record)
        assert reloaded(path).runs[0].records[0].destination == "/trash/a.bin"

    def test_the_amendment_does_not_add_a_record(self, tmp_path):
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        record = DeletionRecord("/d/a.bin")
        log.record(run, record)
        record.destination = "/trash/a.bin"
        log.record_destination(run, record)
        assert counts(reloaded(path)) == (1, 1), "the amendment was read as a second deletion"

    def test_an_amendment_lost_to_a_crash_leaves_the_record_intact(self, tmp_path):
        # The record is what matters; the destination only decides whether a restore is offered,
        # and "" already means "not offered" everywhere else.
        path = tmp_path / "log.jsonl"
        log = DeletionLog(path)
        run = log.start_run(permanent=False)
        log.record(run, DeletionRecord("/d/a.bin", size=7))
        record = reloaded(path).runs[0].records[0]
        assert record.original_path == "/d/a.bin"
        assert record.size == 7
        assert record.destination == ""
        assert record.restorable is False

    def test_an_amendment_for_an_unknown_record_is_ignored(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path, runs=1, per_run=1)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps({"run": "no-such-run", "amend": "/nope", "destination": "/x"}) + "\n")
        assert counts(reloaded(path)) == (1, 1)


class TestUpgradingFromTheOldFormat:
    """An upgrade must not silently discard the undo history it finds."""

    LEGACY = (
        '<deletion_log><run id="r1" started_at="2026-01-01T00:00:00" permanent="False">'
        '<file path="/d/a.bin" size="42" permanent="False" digest="abc" '
        'destination="/trash/a.bin" reference="/d/ref.bin"/>'
        '<file path="/d/b.bin" size="7" permanent="True"/>'
        "</run></deletion_log>"
    )

    def test_an_xml_log_at_the_new_path_is_read_and_converted(self, tmp_path):
        path = tmp_path / "deletion_log.jsonl"
        path.write_text(self.LEGACY, encoding="utf-8")
        log = reloaded(path)

        assert counts(log) == (1, 2)
        first = log.runs[0].records[0]
        assert first.original_path == "/d/a.bin"
        assert first.size == 42
        assert first.digest == "abc"
        assert first.destination == "/trash/a.bin"
        assert first.reference_path == "/d/ref.bin"
        assert log.runs[0].records[1].permanent is True
        # Converted in place, so the next load takes the fast path.
        assert not path.read_text(encoding="utf-8").lstrip().startswith("<")
        assert counts(reloaded(path)) == (1, 2), "the converted log did not read back"

    def test_the_old_file_beside_the_new_path_is_picked_up(self, tmp_path):
        # What actually happens on upgrade: the app now looks for deletion_log.jsonl, and only
        # deletion_log.xml exists.
        (tmp_path / "deletion_log.xml").write_text(self.LEGACY, encoding="utf-8")
        log = reloaded(tmp_path / "deletion_log.jsonl")
        assert counts(log) == (1, 2)
        assert (tmp_path / "deletion_log.jsonl").exists(), "the log was not carried forward"

    def test_a_damaged_old_log_does_not_raise(self, tmp_path):
        # XML cannot be partially parsed, which is the reason for the change rather than an
        # argument against carrying it over. It must still not break startup.
        path = tmp_path / "deletion_log.jsonl"
        path.write_text("<deletion_log><run id=", encoding="utf-8")
        assert len(reloaded(path)) == 0

    def test_the_default_path_is_the_new_format(self):
        from core.deletion_log import default_log_path

        assert default_log_path("/appdata").endswith(".jsonl")


class TestHousekeeping:
    def test_old_runs_are_dropped_on_load(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path, runs=DeletionLog.MAX_RUNS + 10, per_run=1)
        log = reloaded(path)
        assert len(log) == DeletionLog.MAX_RUNS

    def test_trimming_rewrites_the_file_so_it_stays_bounded(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path, runs=DeletionLog.MAX_RUNS + 10, per_run=1)
        reloaded(path)  # compacts
        assert len(path.read_text(encoding="utf-8").splitlines()) == DeletionLog.MAX_RUNS

    def test_the_newest_runs_are_the_ones_kept(self, tmp_path):
        path = tmp_path / "log.jsonl"
        build(path, runs=DeletionLog.MAX_RUNS + 3, per_run=1)
        log = reloaded(path)
        kept = {run.records[0].original_path for run in log.runs}
        assert "/d/r0f0.bin" not in kept, "the oldest run survived the trim"
        assert f"/d/r{DeletionLog.MAX_RUNS + 2}f0.bin" in kept, "the newest run was dropped"

    def test_compaction_is_atomic(self, tmp_path, monkeypatch):
        """A failed compaction must leave the previous log, not a half-written one."""
        path = tmp_path / "log.jsonl"
        build(path, runs=2, per_run=2)
        before = path.read_text(encoding="utf-8")

        log = DeletionLog(path)
        log.load()

        def failing_replace(src, dst):
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", failing_replace)
        log.save()

        assert path.read_text(encoding="utf-8") == before, "the log was damaged by a failed save"
        assert counts(reloaded(path)) == (2, 4)

    def test_an_unwritable_log_does_not_break_the_deletion(self, tmp_path, monkeypatch):
        # Pre-existing guarantee, re-asserted against the append path: the user asked to delete
        # files, not to maintain a log.
        def refuse(*args, **kwargs):
            raise OSError("read-only filesystem")

        log = DeletionLog(tmp_path / "log.jsonl")
        run = log.start_run(permanent=False)
        monkeypatch.setattr("builtins.open", refuse)
        log.record(run, DeletionRecord("/d/a.bin"))
        assert len(run) == 1, "the record was lost from memory as well as from disk"


class TestTheRealDeletionPath:
    """Through DupeGuru rather than the log alone, since that is where the cost was paid."""

    @pytest.fixture
    def app(self, tmp_path):
        import cli
        from core.app import AppMode, DupeGuru
        from core.scanner import ScanType

        folder = tmp_path / "files"
        folder.mkdir()
        for i in range(6):
            (folder / f"f{i}.bin").write_bytes(b"identical" * 20)
        app = DupeGuru(view=cli._HeadlessView())
        app.app_mode = AppMode.STANDARD
        app.options["scan_type"] = ScanType.CONTENTS
        app.directories.add_path(folder)
        cli._run_scan(app, verbose=False)
        app.deletion_log = DeletionLog(tmp_path / "log.jsonl")
        return app

    def test_a_deletion_writes_one_line_per_file_plus_its_amendment(self, app, tmp_path):
        from hscommon.jobprogress import job

        app.results.mark_all()
        marked = app.results.mark_count
        app._do_delete(job.nulljob, False, False, True)  # permanent: no trash amendments

        lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == marked, f"expected one line per deleted file, got {len(lines)}"

    def test_the_deletion_is_readable_afterwards(self, app, tmp_path):
        from hscommon.jobprogress import job

        app.results.mark_all()
        marked = app.results.mark_count
        app._do_delete(job.nulljob, False, False, True)
        assert counts(reloaded(tmp_path / "log.jsonl")) == (1, marked)
