# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Elapsed time, rate, and the withheld estimate (issue #132).

The tracker's job is as much about staying quiet as about reporting. A confidently wrong
"2 minutes remaining" shown for half an hour is worse than no estimate at all, so most of
these pin down the conditions under which it declines to answer.

Time is injected throughout. Testing a rate estimator against the real clock would mean
either sleeping for the length of the measurement window or asserting nothing much.
"""

import pytest

from hscommon.jobprogress.tracker import (
    MIN_ELAPSED_FOR_ESTIMATE,
    MIN_STABILITY_SPAN,
    MIN_UNITS_FOR_ESTIMATE,
    ProgressTracker,
    format_duration,
    format_rate,
)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tracker(clock):
    return ProgressTracker(clock=clock)


def run_at(tracker, clock, rate, seconds, total=None, start=0):
    """Report *rate* units per second for *seconds*, one report per second."""
    done = start
    for _ in range(int(seconds)):
        clock.advance(1.0)
        done += rate
        tracker.report(int(done), total)
    return int(done)


class TestElapsed:
    def test_elapsed_tracks_the_clock(self, tracker, clock):
        clock.advance(75.0)
        assert tracker.elapsed == 75.0

    def test_elapsed_spans_phases(self, tracker, clock):
        run_at(tracker, clock, 10, 5, total=1000)
        run_at(tracker, clock, 10, 5, total=None)
        # The whole job's elapsed time, not the current phase's -- the user is waiting on the
        # scan, not on whichever internal stage it happens to be in.
        assert tracker.elapsed == 10.0
        # The second phase began on its first report, one tick into its five.
        assert tracker.phase_elapsed == 4.0


class TestRate:
    def test_no_rate_from_a_single_sample(self, tracker, clock):
        tracker.report(10)
        assert tracker.rate is None

    def test_no_rate_until_the_samples_span_enough_time(self, tracker, clock):
        # Two reports milliseconds apart give an arithmetically valid, useless number.
        tracker.report(0)
        clock.advance(0.01)
        tracker.report(500)
        assert tracker.rate is None

    def test_rate_reflects_throughput(self, tracker, clock):
        run_at(tracker, clock, 200, 10)
        assert tracker.rate == pytest.approx(200, rel=0.05)

    def test_rate_follows_a_slowdown_rather_than_averaging_it(self, tracker, clock):
        # The point of the trailing window. A phase that starts on warm cache and continues on
        # cold metadata must report what it is doing now, not a blend dragged toward the fast
        # start -- that blend is exactly what produces a wrong estimate.
        done = run_at(tracker, clock, 1000, 20)
        run_at(tracker, clock, 10, 40, start=done)
        assert tracker.rate == pytest.approx(10, rel=0.2)

    def test_a_stalled_job_reports_zero_not_a_stale_rate(self, tracker, clock):
        run_at(tracker, clock, 200, 10)
        done = tracker._done
        for _ in range(40):
            clock.advance(1.0)
            tracker.report(done)
        assert tracker.rate == 0.0


class TestPhaseBoundaries:
    def test_a_new_total_starts_a_new_phase(self, tracker, clock):
        run_at(tracker, clock, 1000, 10, total=100000)
        tracker.report(5, total=50)
        # Hashing 50 files is not a continuation of reading 100,000 of them. Carrying the
        # samples over would report the old phase's speed for the new phase's work.
        assert tracker.rate is None
        assert tracker.phase_elapsed == 0.0

    def test_a_counter_going_backwards_starts_a_new_phase(self, tracker, clock):
        run_at(tracker, clock, 100, 10, total=None)
        tracker.report(1, total=None)
        assert tracker.rate is None


class TestEstimateIsWithheld:
    """Every one of these is a way the estimate could be confidently wrong."""

    def test_no_estimate_without_a_total(self, tracker, clock):
        # Collection cannot know how many files it will find until it has found them.
        run_at(tracker, clock, 500, 120, total=None)
        assert tracker.rate is not None, "a rate is still available and still honest"
        assert tracker.remaining_seconds is None

    def test_no_estimate_before_the_phase_has_run_long_enough(self, tracker, clock):
        run_at(tracker, clock, 500, int(MIN_ELAPSED_FOR_ESTIMATE) - 5, total=1_000_000)
        assert tracker.remaining_seconds is None

    def test_no_estimate_from_too_few_units(self, tracker, clock):
        # A long phase that has barely moved: elapsed is satisfied, the sample is not.
        run_at(tracker, clock, 1, int(MIN_ELAPSED_FOR_ESTIMATE) + 20, total=1_000_000)
        assert tracker._done < MIN_UNITS_FOR_ESTIMATE
        assert tracker.remaining_seconds is None

    def test_no_estimate_while_the_pace_is_still_changing(self, tracker, clock):
        # The case the issue names: a phase that starts on warm cache and continues on cold
        # metadata. An estimate taken from the fast part is wrong for all of the rest.
        done = run_at(tracker, clock, 2000, 40, total=10_000_000)
        run_at(tracker, clock, 20, 10, total=10_000_000, start=done)
        assert tracker.rate is not None, "the rate itself is still a measurement, still honest"
        assert not tracker.is_stable
        assert tracker.remaining_seconds is None

    def test_no_estimate_when_stalled(self, tracker, clock):
        run_at(tracker, clock, 500, 60, total=10_000_000)
        done = tracker._done
        for _ in range(60):
            clock.advance(1.0)
            tracker.report(done, 10_000_000)
        # Dividing what is left by a rate of zero is infinity, not an estimate.
        assert tracker.remaining_seconds is None

    def test_no_estimate_once_the_work_is_done(self, tracker, clock):
        run_at(tracker, clock, 100, 60, total=6000)
        assert tracker.remaining_seconds is None


class TestEstimateIsOffered:
    def test_a_steady_phase_eventually_gets_an_estimate(self, tracker, clock):
        run_at(tracker, clock, 500, 60, total=100_000)
        assert tracker.is_stable
        remaining = tracker.remaining_seconds
        assert remaining is not None
        # 100,000 total, 30,000 done, 500/s -> 140s.
        assert remaining == pytest.approx(140, rel=0.1)

    def test_the_estimate_needs_a_span_of_agreement_not_a_share_of_the_work(self, tracker, clock):
        # A million-file phase does not have to finish a tenth of itself before it can say
        # anything; it has to hold a steady pace for a while.
        run_at(tracker, clock, 500, int(MIN_STABILITY_SPAN) + 2, total=10_000_000)
        assert tracker.remaining_seconds is not None

    def test_second_to_second_noise_is_smoothed_rather_than_treated_as_instability(self, tracker, clock):
        # Files are not uniformly sized and the per-file cost is spiky. Alternating fast and
        # slow seconds is what normal work looks like, not a reason to refuse an estimate --
        # smoothing that is what the trailing window is for.
        done = 0
        for i in range(60):
            clock.advance(1.0)
            done += 900 if i % 2 else 100
            tracker.report(done, 10_000_000)
        assert tracker.is_stable
        assert tracker.remaining_seconds is not None

    def test_an_estimate_is_withdrawn_when_the_pace_changes(self, tracker, clock):
        done = run_at(tracker, clock, 2000, 40, total=10_000_000)
        assert tracker.remaining_seconds is not None
        run_at(tracker, clock, 20, 10, total=10_000_000, start=done)
        # Having once been confident is not a reason to stay confident.
        assert tracker.remaining_seconds is None

    def test_a_slowdown_is_caught_promptly_not_eventually(self, tracker, clock):
        # Regression guard for a stability check that compares successive readings of the same
        # trailing window. That version never fires at all: the window smooths the transition
        # into a slide of a few percent per second, every reading agrees with its neighbour,
        # and a hundredfold slowdown passes as steady while the estimate silently triples.
        done = run_at(tracker, clock, 2000, 40, total=10_000_000)
        for elapsed in range(1, 16):
            run_at(tracker, clock, 20, 1, total=10_000_000, start=done)
            done += 20
            if not tracker.is_stable:
                break
        assert not tracker.is_stable, "a 100x slowdown was never detected"
        assert elapsed <= 10, f"took {elapsed}s to notice a 100x slowdown"


class TestSummary:
    def test_nothing_to_say_at_the_very_start(self, tracker):
        assert tracker.summary() == ""

    def test_elapsed_alone_before_a_rate_exists(self, tracker, clock):
        clock.advance(5.0)
        tracker.report(3)
        assert tracker.summary() == "5s elapsed"

    def test_rate_joins_once_measurable(self, tracker, clock):
        run_at(tracker, clock, 400, 10, total=None)
        summary = tracker.summary()
        assert "elapsed" in summary
        assert "files/s" in summary
        assert "remaining" not in summary, "no total means no estimate"

    def test_the_estimate_joins_once_trustworthy(self, tracker, clock):
        run_at(tracker, clock, 500, 60, total=100_000)
        assert "remaining" in tracker.summary()

    def test_the_unit_is_the_caller_s(self, tracker, clock):
        run_at(tracker, clock, 400, 10)
        assert "pictures/s" in tracker.summary(unit="pictures")


class TestFormatting:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "0s"), (45, "45s"), (59.9, "59s"), (60, "1m 00s"), (252, "4m 12s"), (3600, "1h 00m"), (5520, "1h 32m")],
    )
    def test_durations_use_the_two_units_that_matter(self, seconds, expected):
        assert format_duration(seconds) == expected

    @pytest.mark.parametrize(
        "rate,expected",
        [(0.004, "0.00 files/s"), (3.5, "3.50 files/s"), (42.7, "42.7 files/s"), (12345.6, "12,346 files/s")],
    )
    def test_rates_keep_precision_where_it_carries_the_signal(self, rate, expected):
        # Cold external metadata runs at a few hundred a second and a warm cache at tens of
        # thousands. A fixed number of decimals is noise at one end or useless at the other.
        assert format_rate(rate) == expected


class TestWiredIntoJob:
    """The tracker is only useful if the scan's phases actually feed it."""

    def _job(self):
        from hscommon.jobprogress.job import Job

        return Job(1, lambda *args: True)

    def test_iter_with_progress_reports_its_units(self):
        job = self._job()
        list(job.iter_with_progress(list(range(50)), every=10))
        assert job.tracker._done == 50
        assert job.tracker._total == 50, "a counted phase knows its total, so it can be estimated"

    def test_subjobs_share_the_parent_s_tracker(self):
        # Timing must span the whole scan. A tracker per nesting level would restart elapsed
        # time at every internal stage and never accumulate enough history to say anything.
        job = self._job()
        child = job.start_subjob(2)
        grandchild = child.start_subjob(2)
        assert child.tracker is job.tracker
        assert grandchild.tracker is job.tracker

    def test_percentage_plumbing_does_not_feed_the_tracker(self):
        # set_progress carries a percentage blended across phases of very different cost.
        # Treating it as units would produce an estimate from a number that cannot support one.
        job = self._job()
        job.start_job(100, "")
        job.set_progress(40)
        assert job.tracker._done == 0

    def test_the_null_job_tracks_nothing_and_raises_nothing(self):
        # nulljob is the default for every j= parameter in core, so this runs constantly in
        # tests and in any headless caller that does not pass a job.
        from hscommon.jobprogress.job import nulljob

        nulljob.report_units(5, 10)
        assert list(nulljob.iter_with_progress([1, 2, 3])) == [1, 2, 3]


class TestWiredIntoCollection:
    """Collection is the phase the issue is actually about."""

    def test_collection_reports_files_without_claiming_a_total(self, tmp_path):
        from core import fs
        from core.directories import Directories
        from hscommon.jobprogress.job import Job

        for i in range(30):
            (tmp_path / f"f{i}.txt").write_text(str(i))
        directories = Directories()
        directories.add_path(tmp_path)
        job = Job(1, lambda *args: True)
        files = list(directories.get_files(fileclasses=[fs.File], j=job))

        assert len(files) == 30
        assert job.tracker._done == 30
        # The walk does not know how many files it will find until it has found them, so it
        # gets elapsed time and a rate but must never offer a countdown.
        assert job.tracker._total is None
        assert job.tracker.remaining_seconds is None


class TestWiredIntoTheProgressWindow:
    """The path from tracker to the label the user actually reads."""

    def _window(self):
        from hscommon.gui.progress_window import ProgressWindow

        window = ProgressWindow(lambda jobid: None)
        for field in (window.jobdesc_textfield, window.progressdesc_textfield, window.timedesc_textfield):
            field.view = type("V", (), {"refresh": lambda self: None})()
        window.view = type(
            "V", (), {"show": lambda self: None, "close": lambda self: None, "set_progress": lambda self, p: None}
        )()
        window.job_cancelled = False
        return window

    def test_pulse_publishes_the_timing_summary(self, clock):
        window = self._window()
        window.progress_tracker = ProgressTracker(clock=clock)
        run_at(window.progress_tracker, clock, 400, 10, total=None)
        window._job_running = True
        window.last_progress = 40
        window.last_desc = "Collected 4,000 files to scan"

        window.pulse()

        assert "elapsed" in window.timedesc_textfield.text
        assert "files/s" in window.timedesc_textfield.text
        # The phase's own message is untouched: timing is additive, not a replacement.
        assert window.progressdesc_textfield.text == "Collected 4,000 files to scan"

    def test_timing_is_cleared_between_jobs(self, clock):
        # A stale "1h 04m elapsed" left over from the previous scan would be worse than blank.
        window = self._window()
        window.timedesc_textfield.text = "1h 04m elapsed, 3 files/s"
        window.run("jobid", "Title", lambda j: None)
        while window._job_running:
            pass
        assert window.timedesc_textfield.text == ""

    def test_a_job_that_never_reports_units_says_nothing_about_rate(self, clock):
        # Not every phase feeds the tracker, and one that does not must not produce a made-up
        # rate from the percentage plumbing.
        window = self._window()
        window.progress_tracker = ProgressTracker(clock=clock)
        clock.advance(30.0)
        window._job_running = True
        window.last_progress = 50
        window.last_desc = "Almost done!"

        window.pulse()

        assert "files/s" not in window.timedesc_textfield.text
        assert "elapsed" in window.timedesc_textfield.text, "elapsed time is always honest"
