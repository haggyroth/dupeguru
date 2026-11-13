# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Sending many files to the Recycle Bin in one call, without changing what a deletion means.

Recycling was one shell call per file: measured on a local disk, 25 ms a file against 8 ms in a
batch, and a pooled volume where every file operation is slow pays that fixed cost per file. A
user who marked 184,000 files found it painfully slow.

The risk in batching is not speed, it is what each file's outcome means: this is the code that
removes files. So the tests concentrate on the questions that matter there -- does a file that
failed stay marked and on disk, does one bad file stall the rest, can a completed deletion ever be
reported as a failure, is the log still written before anything is removed -- and on the route
being *equivalent* to the per-file one, not merely faster.

The Windows shell is faked (as in trash_test.py), so all of this runs on every platform.
"""

import shutil
import sys
from pathlib import Path

import pytest

import cli
import core.app as core_app
from core import trash
from core.app import AppMode, DupeGuru
from core.scanner import ScanType
from core.tests.trash_test import FakeSink, install_fake_windows
from core.trash import TrashOutcome
from hscommon.jobprogress.job import JobCancelled


class TestTrashFiles:
    """The platform-independent logic: outcomes per file, and when batching is attempted."""

    def test_each_file_gets_its_own_outcome(self):
        def single(path):
            if path.endswith("bad"):
                raise OSError("denied")
            return path + "->bin"

        outcomes = trash.trash_files(["a", "bad", "c"], single=single)
        assert [o.destination for o in outcomes] == ["a->bin", "", "c->bin"]
        assert outcomes[0].error is None and outcomes[2].error is None
        assert isinstance(outcomes[1].error, OSError)

    def test_a_failure_does_not_stop_the_files_after_it(self):
        seen = []

        def single(path):
            seen.append(path)
            if path == "b":
                raise OSError("no")
            return ""

        trash.trash_files(["a", "b", "c", "d"], single=single)
        assert seen == ["a", "b", "c", "d"]

    def test_the_error_is_returned_not_raised(self):
        [outcome] = trash.trash_files(["x"], single=lambda p: (_ for _ in ()).throw(ValueError("odd")))
        assert isinstance(outcome.error, ValueError)

    def test_no_paths_is_no_outcomes(self):
        assert trash.trash_files([]) == []

    def test_a_substituted_single_file_function_is_never_batched_around(self, monkeypatch):
        # The tests that replace trash_file must not have real files sent to the real bin behind
        # their back, and a caller that substituted its own function wants that one used.
        monkeypatch.setattr(trash, "can_batch", lambda: True)
        monkeypatch.setattr(
            trash, "_trash_windows_batch", lambda paths: pytest.fail("batched despite a substituted function")
        )
        outcomes = trash.trash_files(["a", "b"], single=lambda p: "ok")
        assert [o.destination for o in outcomes] == ["ok", "ok"]

    def test_the_real_function_is_batched_when_the_platform_can(self, monkeypatch):
        calls = []
        monkeypatch.setattr(trash, "can_batch", lambda: True)
        monkeypatch.setattr(trash, "trash_file", lambda p: pytest.fail("fell back although the batch settled all"))

        def batch(paths):
            calls.append(list(paths))
            return {i: TrashOutcome(f"bin/{p}", None) for i, p in enumerate(paths)}

        monkeypatch.setattr(trash, "_trash_windows_batch", batch)
        outcomes = trash.trash_files(["a", "b", "c"])
        assert calls == [["a", "b", "c"]]
        assert [o.destination for o in outcomes] == ["bin/a", "bin/b", "bin/c"]

    def test_a_single_file_is_not_worth_a_batch(self, monkeypatch):
        monkeypatch.setattr(trash, "can_batch", lambda: True)
        monkeypatch.setattr(trash, "_trash_windows_batch", lambda paths: pytest.fail("batched one file"))
        monkeypatch.setattr(trash, "trash_file", lambda p: "bin")
        assert trash.trash_files(["only"])[0].destination == "bin"

    def test_where_batching_is_not_available_it_goes_one_at_a_time(self, monkeypatch):
        monkeypatch.setattr(trash, "can_batch", lambda: False)
        monkeypatch.setattr(trash, "_trash_windows_batch", lambda paths: pytest.fail("batched where it cannot"))
        monkeypatch.setattr(trash, "trash_file", lambda p: "bin/" + p)
        assert [o.destination for o in trash.trash_files(["a", "b"])] == ["bin/a", "bin/b"]


class TestWhenABatchStopsPartWay:
    """The shell aborts the rest of a batch at the first failure. What is left must still be done."""

    @pytest.fixture
    def scripted(self, monkeypatch):
        """Batch results played back in order, with the single-file route recorded."""
        monkeypatch.setattr(trash, "can_batch", lambda: True)
        state = {"batches": [], "singles": [], "script": [], "single_errors": set()}

        def batch(paths):
            state["batches"].append(list(paths))
            settled_upto = min(state["script"].pop(0) if state["script"] else 0, len(paths))
            return {i: TrashOutcome(f"bin/{paths[i]}", None) for i in range(settled_upto)}

        def single(path):
            state["singles"].append(path)
            if path in state["single_errors"]:
                raise OSError(f"cannot trash {path}")
            return f"bin/{path}"

        monkeypatch.setattr(trash, "_trash_windows_batch", batch)
        monkeypatch.setattr(trash, "trash_file", single)
        return state

    def test_the_unsettled_file_is_done_alone_and_the_rest_go_round_again(self, scripted):
        scripted["script"] = [2, 3]  # first batch settles a,b; second (c removed) settles all three
        outcomes = trash.trash_files(["a", "b", "c", "d", "e", "f"])
        assert scripted["singles"] == ["c"], "only the file that stopped the batch is done on its own"
        assert scripted["batches"] == [["a", "b", "c", "d", "e", "f"], ["d", "e", "f"]]
        assert all(o.error is None for o in outcomes)
        assert [o.destination for o in outcomes] == [f"bin/{n}" for n in "abcdef"]

    def test_a_file_that_always_fails_cannot_stall_the_others(self, scripted):
        scripted["script"] = [1, 1, 1]
        scripted["single_errors"] = {"b", "d"}
        outcomes = trash.trash_files(["a", "b", "c", "d", "e"])
        errors = [o.error for o in outcomes]
        assert errors[0] is None and errors[2] is None and errors[4] is None
        assert isinstance(errors[1], OSError) and isinstance(errors[3], OSError)

    def test_a_first_file_that_fails_does_not_end_batching_for_the_chunk(self, scripted):
        # An empty batch can just mean its first file was the one that failed.
        scripted["script"] = [0, 99]
        scripted["single_errors"] = {"a"}
        outcomes = trash.trash_files(["a", "b", "c", "d"])
        assert scripted["singles"] == ["a"]
        assert len(scripted["batches"]) == 2
        assert isinstance(outcomes[0].error, OSError)
        assert all(o.error is None for o in outcomes[1:])

    def test_two_empty_batches_in_a_row_give_up_on_batching(self, scripted):
        scripted["script"] = [0, 0, 5, 5]
        outcomes = trash.trash_files(["a", "b", "c", "d", "e"])
        assert len(scripted["batches"]) == 2, "no third attempt once bulk has failed twice"
        assert [o.destination for o in outcomes] == [f"bin/{n}" for n in "abcde"]
        assert all(o.error is None for o in outcomes)

    def test_a_successful_batch_resets_the_count_of_empty_ones(self, scripted):
        # "Two empty batches in a row", not "two empty batches ever": a good batch between them
        # shows bulk works here, so the next empty one is just another bad first file.
        scripted["script"] = [0, 2, 0, 9]
        outcomes = trash.trash_files([f"p{i}" for i in range(8)])
        assert len(scripted["batches"]) == 4
        assert all(o.error is None for o in outcomes)

    def test_every_file_ends_up_with_an_outcome(self, scripted):
        scripted["script"] = [1, 0, 0, 0]
        outcomes = trash.trash_files(list("abcdefgh"))
        assert len(outcomes) == 8 and all(o is not None for o in outcomes)


class FakeItem:
    def __init__(self, path):
        self.path = path

    def GetDisplayName(self, flags):
        return self.path


class TestTheWindowsBatch:
    """_trash_windows_batch against a faked shell: what it trusts, and what it does not."""

    @pytest.fixture
    def files(self, tmp_path):
        paths = []
        for name in "abcd":
            p = tmp_path / f"{name}.txt"
            p.write_text(name)
            paths.append(str(p))
        return paths

    def install(self, monkeypatch, behaviour):
        """behaviour(paths, sink) plays the shell: reports each item to the sink, may raise."""
        seen = {}

        def fake_send2trash(paths):
            seen["paths"] = list(paths)
            sink = sys.modules["send2trash.win.modern"].create_sink()[1]
            behaviour(paths, sink)

        modern = install_fake_windows(monkeypatch, fake_send2trash, sink_factory=FakeSink)
        return modern, seen

    def test_every_file_the_shell_recycled_is_settled_with_its_destination(self, monkeypatch, files):
        def behaviour(paths, sink):
            for path in paths:
                sink.PostDeleteItem(0, FakeItem(path), 0, FakeItem(r"C:\$Recycle.Bin\x\$R" + path[-5:]))
                Path(path).unlink()

        self.install(monkeypatch, behaviour)
        settled = trash._trash_windows_batch(files)
        assert sorted(settled) == [0, 1, 2, 3]
        assert settled[0].destination.startswith(r"C:\$Recycle.Bin")
        assert all(o.error is None for o in settled.values())

    def test_all_files_go_in_one_call(self, monkeypatch, files):
        modern, seen = self.install(monkeypatch, lambda paths, sink: None)
        trash._trash_windows_batch(files)
        assert seen["paths"] == files

    def test_an_abort_settles_only_what_was_done(self, monkeypatch, files):
        def behaviour(paths, sink):
            for path in paths[:2]:
                sink.PostDeleteItem(0, FakeItem(path), 0, FakeItem("bin/" + path))
                Path(path).unlink()
            raise OSError(None, "aborted", paths[2], -2147467259)

        self.install(monkeypatch, behaviour)
        assert sorted(trash._trash_windows_batch(files)) == [0, 1]

    def test_a_failure_reported_for_a_file_still_on_disk_is_not_settled(self, monkeypatch, files):
        def behaviour(paths, sink):
            sink.PostDeleteItem(0, FakeItem(paths[0]), -2147467259, None)  # E_FAIL, file untouched

        self.install(monkeypatch, behaviour)
        assert trash._trash_windows_batch(files) == {}, "a failed delete must be left for the single route to retry"

    def test_a_file_that_is_gone_counts_as_trashed_whatever_was_reported(self, monkeypatch, files):
        # Recycled, but reported with an unexpected code. Retrying it would raise "not found" and
        # turn a completed deletion into a reported failure -- the shape of the macOS bug in #201.
        def behaviour(paths, sink):
            sink.PostDeleteItem(0, FakeItem(paths[0]), 1, FakeItem("bin/a"))
            Path(paths[0]).unlink()

        self.install(monkeypatch, behaviour)
        settled = trash._trash_windows_batch(files)
        assert settled[0].error is None and settled[0].destination == "bin/a"

    def test_a_file_the_batch_never_reached_is_not_settled(self, monkeypatch, files):
        self.install(monkeypatch, lambda paths, sink: (_ for _ in ()).throw(OSError("nothing happened")))
        assert trash._trash_windows_batch(files) == {}

    def test_the_sink_factory_is_put_back(self, monkeypatch, files):
        modern, _ = self.install(monkeypatch, lambda paths, sink: None)
        before = modern.create_sink
        trash._trash_windows_batch(files)
        assert modern.create_sink is before

    def test_the_sink_factory_is_put_back_even_when_something_unexpected_happens(self, monkeypatch, files):
        modern, _ = self.install(monkeypatch, lambda paths, sink: (_ for _ in ()).throw(RuntimeError("boom")))
        before = modern.create_sink
        with pytest.raises(RuntimeError):
            trash._trash_windows_batch(files)
        assert modern.create_sink is before

    def test_a_path_is_matched_whatever_its_spelling(self, monkeypatch, files):
        # The shell reports the path its own way; matching must not depend on case or separators.
        def behaviour(paths, sink):
            for path in paths:
                sink.PostDeleteItem(0, FakeItem(path.upper() if sys.platform == "win32" else path), 0, None)
                Path(path).unlink()

        self.install(monkeypatch, behaviour)
        assert sorted(trash._trash_windows_batch(files)) == [0, 1, 2, 3]

    def test_missing_pywin32_settles_nothing_and_is_not_an_error(self, monkeypatch, files):
        monkeypatch.setitem(sys.modules, "pythoncom", None)
        assert trash._trash_windows_batch(files) == {}


def fake_bin(tmp_path):
    """A stand-in for the Recycle Bin: files move into a folder, and the move is reported."""
    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    moved = []

    def fake_trash(path):
        target = bin_dir / f"{len(moved):06d}_{path.replace(':', '').replace('\\', '_').replace('/', '_')}"
        shutil.move(path, str(target))
        moved.append(path)
        return str(target)

    return bin_dir, moved, fake_trash


class TestTheChunkRoute:
    """The app's batched deletion, against real files from a real scan."""

    @pytest.fixture
    def make_app(self, tmp_path, monkeypatch):
        def build(pairs=6):
            for i in range(pairs):
                (tmp_path / f"keep_{i:03d}.txt").write_bytes(f"contents of pair {i} ".encode() * 3)
                (tmp_path / f"dupe_{i:03d}.txt").write_bytes(f"contents of pair {i} ".encode() * 3)
            app = DupeGuru(view=cli._HeadlessView())
            app.directory_tree.view = type("V", (), {"refresh": lambda s: None, "refresh_states": lambda s: None})()
            app.app_mode = AppMode.STANDARD
            app.options["scan_type"] = ScanType.CONTENTS
            app.directories.add_path(tmp_path)
            cli._run_scan(app, verbose=False)
            app.deletion_log.path = str(tmp_path / "log.xml")
            # The scanner decides which copy of each pair is the reference, so which file names are
            # marked is not ours to choose: mark whatever it calls a duplicate.
            app.results.mark_all()
            return app

        monkeypatch.setattr(core_app, "can_batch", lambda: True)
        return build

    @staticmethod
    def nth(app, n):
        """The path of the n-th marked duplicate, in a stable order."""
        return sorted((d.path for d in app.results.dupes), key=lambda p: p.name)[n]

    def run(self, app, *, link=False):
        app._do_delete(app.progress_window.create_job(), link, True, False, False)

    def test_marked_files_are_removed_and_leave_the_results(self, tmp_path, make_app, monkeypatch):
        app = make_app()
        bin_dir, moved, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        self.run(app)
        assert len(list(tmp_path.glob("*.txt"))) == 6, "one copy of each pair is kept"
        assert len(moved) == 6
        assert app.results.groups == []
        assert app.results.problems == []

    def test_it_is_the_same_as_the_per_file_route(self, tmp_path, make_app, monkeypatch):
        # Same files, same fake bin: the per-file route and the chunk route must leave the same
        # disk, the same results, and the same log.
        def outcome(batched):
            sub = tmp_path / ("b" if batched else "s")
            sub.mkdir()
            for i in range(5):
                (sub / f"keep_{i}.txt").write_bytes(f"pair {i} ".encode() * 4)
                (sub / f"dupe_{i}.txt").write_bytes(f"pair {i} ".encode() * 4)
            app = DupeGuru(view=cli._HeadlessView())
            app.directory_tree.view = type("V", (), {"refresh": lambda s: None, "refresh_states": lambda s: None})()
            app.app_mode = AppMode.STANDARD
            app.options["scan_type"] = ScanType.CONTENTS
            app.directories.add_path(sub)
            cli._run_scan(app, verbose=False)
            app.deletion_log.path = str(sub / "log.xml")
            app.results.mark_all()
            _, _, fake_trash = fake_bin(sub)
            monkeypatch.setattr(core_app, "trash_file", fake_trash)
            monkeypatch.setattr(core_app, "can_batch", lambda: batched)
            app._do_delete(app.progress_window.create_job(), False, False, False, False)
            run = list(app.deletion_log)[0]
            # Counts and sizes, not names: which copy of a pair the scanner calls the reference is
            # its choice and differs between two scans of identical folders.
            return (
                len(list(sub.glob("*.txt"))),
                len(app.results.groups),
                sorted((r.size, bool(r.destination)) for r in run.records),
            )

        assert outcome(True) == outcome(False)

    def test_one_call_per_chunk_not_per_file(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=5)
        monkeypatch.setattr(core_app, "TRASH_BATCH_SIZE", 2)
        calls = []
        _, _, fake_trash = fake_bin(tmp_path)

        def recording_trash_files(paths, single=None):
            calls.append(len(paths))
            # The app must hand over ITS trash_file: that is how a substituted function is honoured
            # and how real files stay out of the real bin in tests. Asserted here rather than
            # tested by deleting the argument, which would recycle real files if it survived.
            assert single is core_app.trash_file
            return [TrashOutcome(fake_trash(p), None) for p in paths]

        monkeypatch.setattr(core_app, "trash_files", recording_trash_files)
        self.run(app)
        assert calls == [2, 2, 1]
        assert app.results.groups == []

    def test_a_file_that_fails_stays_marked_and_on_disk_and_the_rest_are_removed(self, tmp_path, make_app, monkeypatch):
        app = make_app()
        _, _, fake_trash = fake_bin(tmp_path)

        victim = self.nth(app, 2)

        def picky(path):
            if Path(path) == victim:
                raise OSError("access is denied")
            return fake_trash(path)

        monkeypatch.setattr(core_app, "trash_file", picky)
        self.run(app)
        assert victim.exists(), "a failed file must still be there"
        assert len(list(tmp_path.glob("*.txt"))) == 7, "six kept, one that failed"
        [(problem_dupe, message)] = app.results.problems
        assert problem_dupe.path == victim and "denied" in message
        assert app.results.is_marked(problem_dupe), "left marked so the user can see and retry it"
        assert len(app.results.groups) == 1

    def test_the_log_is_written_before_anything_is_removed(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        _, _, fake_trash = fake_bin(tmp_path)
        on_disk_when_trashing = []

        def watching(path):
            from core.deletion_log import DeletionLog

            reloaded = DeletionLog(app.deletion_log.path)
            reloaded.load()
            on_disk_when_trashing.append(sum(len(r) for r in reloaded))
            return fake_trash(path)

        monkeypatch.setattr(core_app, "trash_file", watching)
        self.run(app)
        assert on_disk_when_trashing[0] == 3, "every record in the chunk is on disk before the first file goes"

    def test_destinations_are_recorded_for_a_restore(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        _, _, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        self.run(app)
        # Read back from disk: the in-memory record is updated either way, and it is the saved log
        # that a restore uses after the program has been closed.
        from core.deletion_log import DeletionLog

        reloaded = DeletionLog(app.deletion_log.path)
        reloaded.load()
        [run] = list(reloaded)
        assert len(run.records) == 3
        assert all(r.destination and r.restorable for r in run.records)

    def test_a_file_already_gone_counts_as_done(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        self.nth(app, 1).unlink()
        _, moved, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        self.run(app)
        assert len(moved) == 2
        assert app.results.problems == []
        assert app.results.groups == []

    def test_a_file_changed_since_the_scan_is_refused_and_untouched(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        victim = self.nth(app, 1)
        victim.write_bytes(b"edited after the scan, so a different size entirely")
        _, moved, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        self.run(app)
        assert victim.read_bytes().startswith(b"edited")
        assert len(moved) == 2
        assert [d.path for d, _ in app.results.problems] == [victim]

    def test_replacement_links_are_made_and_failed_ones_cleaned_up(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        _, _, fake_trash = fake_bin(tmp_path)
        victim = self.nth(app, 1)
        succeeded = [self.nth(app, 0), self.nth(app, 2)]

        def picky(path):
            if Path(path) == victim:
                raise OSError("locked")
            return fake_trash(path)

        monkeypatch.setattr(core_app, "trash_file", picky)
        self.run(app, link=True)
        for path in succeeded:
            assert path.exists(), "a hardlink replaced the removed file"
        assert not list(tmp_path.glob("*.dupeguru-link")), "the failed file's temporary link must not be left behind"
        assert victim.exists()

    def test_progress_is_reported_per_chunk_and_totals_the_marked_files(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=5)
        monkeypatch.setattr(core_app, "TRASH_BATCH_SIZE", 2)
        _, _, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        steps = []

        class Job:
            def start_job(self, count, *a, **k):
                steps.append(("start", count))

            def add_progress(self, progress=1, *a, **k):
                steps.append(("step", progress))

        app._do_delete(Job(), False, False, False, False)
        assert steps[0] == ("start", 5)
        assert [n for kind, n in steps if kind == "step"] == [2, 2, 1]

    def test_cancelling_stops_the_chunks_not_yet_started_and_records_no_problem_for_them(
        self, tmp_path, make_app, monkeypatch
    ):
        app = make_app(pairs=6)
        monkeypatch.setattr(core_app, "TRASH_BATCH_SIZE", 2)
        _, moved, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        chunks_started = []

        class Job:
            def start_job(self, *a, **k):
                pass

            def add_progress(self, progress=1, *a, **k):
                chunks_started.append(progress)
                if len(chunks_started) == 2:
                    raise JobCancelled()

        app._do_delete(Job(), False, False, False, False)
        assert len(moved) == 2, "only the first chunk ran"
        assert app.results.problems == []
        remaining = [d for d in app.results.dupes if app.results.is_marked(d)]
        assert len(remaining) == 4, "the files not reached stay marked"

    def test_direct_deletion_is_unchanged_and_not_batched(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        monkeypatch.setattr(core_app, "trash_files", lambda *a, **k: pytest.fail("batched a permanent deletion"))
        app._do_delete(app.progress_window.create_job(), False, False, True, False)
        assert len(list(tmp_path.glob("*.txt"))) == 3, "one copy of each pair is kept"
        assert app.results.groups == []

    def test_the_per_file_route_is_used_where_batching_is_not_available(self, tmp_path, make_app, monkeypatch):
        app = make_app(pairs=3)
        monkeypatch.setattr(core_app, "can_batch", lambda: False)
        monkeypatch.setattr(core_app, "trash_files", lambda *a, **k: pytest.fail("batched where it cannot"))
        _, moved, fake_trash = fake_bin(tmp_path)
        monkeypatch.setattr(core_app, "trash_file", fake_trash)
        self.run(app)
        assert len(moved) == 3


class TestPerformOnMarkedBatched:
    @pytest.fixture
    def results(self):
        from core.tests.base import TestApp

        app = TestApp().app
        from core.tests.base import GetTestGroups

        app.results.groups = GetTestGroups()[2]
        app.results.mark_all()
        return app.results

    def test_an_unexpected_exception_in_the_whole_batch_is_recorded_not_raised(self, results):
        def explode(chunk):
            raise RuntimeError("bug")

        results.perform_on_marked_batched(explode, True, 10)
        assert results.problems, "every file of the chunk is reported rather than the loop unwinding"

    def test_a_non_oserror_for_one_file_is_a_problem_for_that_file(self, results):
        results.perform_on_marked_batched(lambda chunk: [ValueError("odd")] + [None] * (len(chunk) - 1), True, 50)
        assert len(results.problems) == 1

    def test_without_remove_from_results_nothing_is_removed(self, results):
        before = len(results.dupes)
        results.perform_on_marked_batched(lambda chunk: [None] * len(chunk), False, 50)
        assert len(results.dupes) == before


@pytest.mark.skipif(sys.platform != "win32" or not trash.can_batch(), reason="needs the real Windows shell")
class TestTheRealRecycleBin:
    """Against the actual shell, because a fake can only confirm what we assumed about it.

    Files are put back from the bin afterwards the way ``restore_record`` does it, so a run leaves
    nothing behind to clear out of the user's Recycle Bin.
    """

    @staticmethod
    def put_back(paths, outcomes):
        for path, outcome in zip(paths, outcomes):
            if outcome.error is None and outcome.destination and Path(outcome.destination).exists():
                shutil.move(outcome.destination, str(path))

    def test_a_batch_recycles_every_file_and_reports_where_each_went(self, tmp_path):
        paths = []
        for i in range(5):
            path = tmp_path / f"real_{i}.bin"
            path.write_bytes(bytes([i]) * 5000)
            paths.append(path)
        outcomes = trash.trash_files([str(p) for p in paths])
        try:
            assert all(o.error is None for o in outcomes)
            assert not any(p.exists() for p in paths), "every file must have left its folder"
            assert all(o.destination and Path(o.destination).exists() for o in outcomes), "and be where it says"
        finally:
            self.put_back(paths, outcomes)
        assert [p.read_bytes() for p in paths] == [bytes([i]) * 5000 for i in range(5)]

    def test_a_file_the_shell_refuses_fails_alone_and_stays_put(self, tmp_path):
        paths = []
        for i in range(4):
            path = tmp_path / f"locked_{i}.bin"
            path.write_bytes(b"x" * 5000)
            paths.append(path)
        # Held open without delete sharing: the shell cannot recycle it.
        held = open(paths[1], "rb")
        try:
            outcomes = trash.trash_files([str(p) for p in paths])
        finally:
            held.close()
        try:
            assert outcomes[1].error is not None
            assert paths[1].exists(), "a refused file must still be on disk"
            assert [o.error for o in outcomes if o is not outcomes[1]] == [None, None, None]
            assert [p.exists() for p in paths] == [False, True, False, False]
        finally:
            self.put_back(paths, outcomes)
