# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Elapsed time, throughput, and -- only when it can be trusted -- a remaining estimate.

A slow scan and a hung scan look identical when the only thing on screen is a rising count.
Collecting a few hundred thousand files from a cold external drive takes tens of minutes at a
few hundred files per second, and there is nothing in that picture to distinguish it from a
wedged process.

Elapsed time and a live rate fix that on their own, and they are always honest: both are
measurements, not predictions. A remaining estimate is a prediction, and this workload is a
bad one to predict from a small sample -- per-file cost varies by three orders of magnitude
between cold and warm metadata. So the estimate is withheld until the rate has actually
settled, and withdrawn again if it stops being settled. Saying nothing is a much smaller
failure than saying "2 minutes remaining" for half an hour.

The rate is measured over a trailing window rather than over the whole run, so that a phase
which starts fast and turns slow (or the reverse) is reported as it is now rather than as an
average dragged toward whatever happened first.
"""

import time
from collections import deque


#: Rate is measured over this many seconds of history. Long enough not to jitter on a stalled
#: moment, short enough to notice when the workload genuinely changes character.
WINDOW_SECONDS = 30.0

#: No rate at all until the window spans at least this long. Two samples milliseconds apart
#: produce arithmetically valid nonsense.
MIN_SPAN_SECONDS = 2.0

#: An estimate needs a phase that has been running at least this long...
MIN_ELAPSED_FOR_ESTIMATE = 15.0

#: ...and has completed at least this many units. Both guard against the first moments of a
#: phase, where the rate reflects caching and startup rather than the work.
MIN_UNITS_FOR_ESTIMATE = 100

#: The two halves of the window must agree within this factor before an estimate is offered.
#: At 1.6, a phase holding 500-700/s speaks and one sliding from 500/s to 900/s stays silent.
STABILITY_RATIO = 1.6

#: Stability needs at least this much history. Shorter than the full window so that an
#: estimate is not withheld for a full window's length at the start of every phase.
MIN_STABILITY_SPAN = 20.0


class ProgressTracker:
    """Timing for one job, shared by its subjobs.

    Fed by :meth:`report`, which takes the number of units finished so far and, when it is
    known, the total. A total of ``None`` means an open-ended phase -- collection does not
    know how many files it will find until it has found them -- which yields a rate but never
    an estimate.
    """

    def __init__(self, clock=time.monotonic):
        # Injected so tests can drive time directly. Monotonic rather than wall clock: this
        # measures durations, and a clock adjustment mid-scan should not produce a negative
        # rate or a jump in elapsed time.
        self._clock = clock
        self._started = self._clock()
        self._samples = deque()  # (timestamp, units done), trimmed to WINDOW_SECONDS
        self._total = None
        self._done = 0
        self._phase_started = self._started

    # --- Feeding

    def report(self, done: int, total=None) -> None:
        """Record that *done* units are finished, out of *total* if that is known."""
        now = self._clock()
        if total != self._total or done < self._done:
            # A different total, or a counter that went backwards, means a new phase. Carrying
            # the old phase's samples across would blend two unrelated workloads -- reading
            # metadata and comparing hashes are not the same units and not the same speed.
            self._start_phase(now, total)
        self._total = total
        self._done = done
        self._samples.append((now, done))
        self._trim(now)

    def _start_phase(self, now: float, total) -> None:
        self._samples.clear()
        self._phase_started = now
        self._done = 0

    def _trim(self, now: float) -> None:
        # Keep two samples regardless of age: a phase reporting less often than the window is
        # long still deserves a rate, even a stale one.
        while len(self._samples) > 2 and now - self._samples[0][0] > WINDOW_SECONDS:
            self._samples.popleft()

    # --- Reading

    @property
    def elapsed(self) -> float:
        """Seconds since the job started, across all its phases."""
        return self._clock() - self._started

    @property
    def phase_elapsed(self) -> float:
        """Seconds since the current phase started."""
        return self._clock() - self._phase_started

    @property
    def rate(self):
        """Units per second over the trailing window, or ``None`` if not yet measurable."""
        if len(self._samples) < 2:
            return None
        (first_at, first_done), (last_at, last_done) = self._samples[0], self._samples[-1]
        span = last_at - first_at
        if span < MIN_SPAN_SECONDS:
            return None
        units = last_done - first_done
        if units <= 0:
            # Genuinely stalled, or a phase that reports time without progress. Zero is a
            # truthful rate; it just cannot produce an estimate (see remaining_seconds).
            return 0.0
        return units / span

    @property
    def is_stable(self) -> bool:
        """Whether the workload has held a steady enough pace to predict from.

        Compares the older half of the window against the newer half. The two are computed
        from disjoint samples, so a change in the character of the work shows up as
        disagreement between them.

        Comparing successive readings of the *same* trailing window instead cannot detect
        this, which is worth stating because it is the obvious implementation and it silently
        does not work: the window smooths a transition into a gradual slide, and each reading
        looks much like the one before it right through a hundredfold slowdown. Every reading
        agrees with its neighbour while the estimate quietly triples.
        """
        if len(self._samples) < 4:
            return False
        span = self._samples[-1][0] - self._samples[0][0]
        if span < MIN_STABILITY_SPAN:
            return False
        midpoint = self._samples[0][0] + span / 2
        older = [s for s in self._samples if s[0] <= midpoint]
        newer = [s for s in self._samples if s[0] >= midpoint]
        if len(older) < 2 or len(newer) < 2:
            return False
        older_rate = _slope(older)
        newer_rate = _slope(newer)
        if older_rate <= 0 or newer_rate <= 0:
            return False
        return max(older_rate, newer_rate) / min(older_rate, newer_rate) <= STABILITY_RATIO

    @property
    def remaining_seconds(self):
        """Estimated seconds left, or ``None`` when no estimate can be trusted.

        Returns ``None`` far more readily than it returns a number. Every condition here is a
        way the estimate could be confidently wrong, and a wrong estimate is worse than the
        elapsed time and rate the caller already has.
        """
        if self._total is None:
            return None  # open-ended phase; nothing to count down to
        if self.phase_elapsed < MIN_ELAPSED_FOR_ESTIMATE:
            return None
        if self._done < MIN_UNITS_FOR_ESTIMATE:
            return None
        if not self.is_stable:
            return None
        rate = self.rate
        if not rate:
            return None
        remaining_units = self._total - self._done
        if remaining_units <= 0:
            return None
        return remaining_units / rate

    def summary(self, unit: str = "files") -> str:
        """A one-line status suffix, or ``""`` when there is nothing worth saying yet.

        Deliberately additive: the caller keeps its own description and appends this, so a
        phase that reports no units still shows its own message unchanged.
        """
        parts = []
        elapsed = self.elapsed
        if elapsed >= 1:
            parts.append(f"{format_duration(elapsed)} elapsed")
        rate = self.rate
        if rate:
            parts.append(f"{format_rate(rate, unit)}")
        remaining = self.remaining_seconds
        if remaining is not None:
            parts.append(f"about {format_duration(remaining)} remaining")
        return ", ".join(parts)


def _slope(samples) -> float:
    """Units per second across a run of samples, or 0.0 if it spans no time."""
    (first_at, first_done), (last_at, last_done) = samples[0], samples[-1]
    span = last_at - first_at
    return (last_done - first_done) / span if span > 0 else 0.0


def format_duration(seconds: float) -> str:
    """A duration in the largest two units that matter: "45s", "4m 12s", "1h 32m"."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_rate(rate: float, unit: str = "files") -> str:
    """A throughput figure with enough precision to be informative at any speed.

    A cold external drive manages a few hundred files a second and a warm cache manages tens
    of thousands, so a fixed number of decimals is either noise at one end or useless at the
    other. Below 10/s the fraction is the whole signal -- "0/s" and "3.5/s" are very different
    situations to be watching.
    """
    if rate >= 100:
        return f"{rate:,.0f} {unit}/s"
    if rate >= 10:
        return f"{rate:.1f} {unit}/s"
    return f"{rate:.2f} {unit}/s"
