# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Parallel picture preparation.

Decoding images is the dominant cost of a first picture scan and was entirely serial. The
work now goes through a process pool, as content-scan hashing already did.

Two things here are easy to get wrong and are asserted rather than assumed: the pool must
produce byte-identical results to the serial path, and it must not be used for small
corpora, where starting workers and importing Qt in each costs more than the work saved.
"""

import pytest

from core.pe import matchblock


class TestParallelThreshold:
    """Measured, not guessed: at 240 pictures an unchunked pool was five times slower."""

    def test_small_corpora_stay_serial(self):
        assert not matchblock._parallel_enabled(1)
        assert not matchblock._parallel_enabled(matchblock.PARALLEL_THRESHOLD - 1)

    def test_large_corpora_use_the_pool(self, monkeypatch):
        monkeypatch.setattr(matchblock.os, "cpu_count", lambda: 8)
        assert matchblock._parallel_enabled(matchblock.PARALLEL_THRESHOLD)
        assert matchblock._parallel_enabled(100000)

    def test_single_core_never_parallelises(self, monkeypatch):
        monkeypatch.setattr(matchblock.os, "cpu_count", lambda: 1)
        assert not matchblock._parallel_enabled(100000)

    def test_unknown_cpu_count_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(matchblock.os, "cpu_count", lambda: None)
        assert not matchblock._parallel_enabled(100000)


class TestPrepareWorker:
    """The worker takes plain data and returns encoded bytes, because it crosses a process
    boundary: the concrete Photo class is chosen at runtime and a spawned child never sees
    it, and a signature pickles as ~700 bytes encoded against ~37 KB inflated."""

    def test_reports_errors_instead_of_raising(self):
        """One unreadable picture must not abandon the scan."""
        result = matchblock.prepare_picture_worker(("/nonexistent/nope.png", "core.pe.photo", "Photo", False, False))
        path, blocks, dimensions, error = result
        assert path == "/nonexistent/nope.png"
        assert blocks is None
        assert error is not None, "a failure must be reported, not silently swallowed"

    def test_unimportable_class_is_reported(self):
        _, blocks, _, error = matchblock.prepare_picture_worker(("/tmp/x.png", "no.such.module", "Nope", False, False))
        assert blocks is None
        assert "Error" in error or "error" in error.lower(), error

    def test_worker_is_module_level_and_picklable(self):
        """ProcessPoolExecutor pickles by reference; a closure or method would not work."""
        import pickle

        pickle.loads(pickle.dumps(matchblock.prepare_picture_worker))


class TestEncodedBlocksNeverReachTheTupleEncoder:
    def test_colors_to_bytes_rejects_bytes(self):
        """bytes(int) yields zero-fill, so this silently corrupted the cache before."""
        from core.pe.cache import colors_to_bytes

        with pytest.raises(TypeError):
            colors_to_bytes(b"\x01\x02\x03")

    def test_tuples_still_encode(self):
        from core.pe.cache import colors_to_bytes

        assert colors_to_bytes([(1, 2, 3)]) == b"\x01\x02\x03"
