# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Persisted picture matches (issue #28, checkpoint 3).

Most of these are about *not* serving a hit. A cache miss costs a rescan; a stale hit shows
the user duplicates that no longer exist, and a result table that disagrees with the disk is
the kind of wrong that costs trust in the tool. So the key has to move for every input that
could change the answer, and each of those is asserted separately rather than trusting one
"key includes everything" test.
"""

from pathlib import Path

import pytest

from core.engine import Match
from core.pe.match_cache import MatchCache, compute_key, default_cache_path


class FakePicture:
    """Only what the cache touches: path, size, mtime."""

    def __init__(self, path, size=100, mtime=1000):
        self.path = Path(path)
        self.size = size
        self.mtime = mtime


@pytest.fixture
def cache(tmp_path):
    c = MatchCache()
    c.connect(tmp_path / "matches.db")
    yield c
    c.close()


def _pics():
    return [FakePicture("/a.jpg"), FakePicture("/b.jpg"), FakePicture("/c.jpg")]


def _matches(pics):
    return [Match(pics[0], pics[1], 95, False), Match(pics[1], pics[2], 80, False)]


class TestRoundTrip:
    def test_stored_matches_come_back(self, cache):
        pics = _pics()
        key = compute_key(pics, 75, False, False)
        cache.put(key, _matches(pics))
        restored = cache.get(key, pics)
        # Compared through Path rather than against literal "/a.jpg": Windows renders the same
        # path with a backslash, so hardcoded separators fail there and nowhere else.
        assert [(m.first.path, m.second.path, m.percentage) for m in restored] == [
            (Path("/a.jpg"), Path("/b.jpg"), 95),
            (Path("/b.jpg"), Path("/c.jpg"), 80),
        ]

    def test_restored_matches_reference_the_current_objects(self, cache):
        """Rebuilt matches must point at this scan's File objects, not stand-ins.

        Everything downstream -- grouping, the results table, deletion -- works on identity.
        """
        pics = _pics()
        key = compute_key(pics, 75, False, False)
        cache.put(key, _matches(pics))
        fresh = _pics()
        restored = cache.get(key, fresh)
        assert restored[0].first is fresh[0]
        assert restored[0].second is fresh[1]

    def test_unknown_key_is_a_miss(self, cache):
        assert cache.get("nope", _pics()) is None


class TestInvalidation:
    """Each input gets its own test. One combined test would pass while missing a field."""

    def _key(self, pics=None, threshold=75, scaled=False, rotated=False):
        return compute_key(pics or _pics(), threshold, scaled, rotated)

    def test_threshold_change_changes_the_key(self):
        assert self._key(threshold=75) != self._key(threshold=80)

    def test_match_scaled_change_changes_the_key(self):
        assert self._key(scaled=False) != self._key(scaled=True)

    def test_match_rotated_change_changes_the_key(self):
        assert self._key(rotated=False) != self._key(rotated=True)

    def test_added_file_changes_the_key(self):
        base = _pics()
        assert self._key(base) != self._key(base + [FakePicture("/d.jpg")])

    def test_removed_file_changes_the_key(self):
        base = _pics()
        assert self._key(base) != self._key(base[:-1])

    def test_edited_file_changes_the_key(self):
        """The case paths alone would miss: same name, different contents.

        Serving old matches here would show duplicates that are no longer duplicates. This is
        deliberately stricter than the directory listing cache in #95, where an in-place edit
        is tolerated because the consequence is a missed match rather than a wrong one.
        """
        base = _pics()
        edited = _pics()
        edited[0].size = 999
        assert self._key(base) != self._key(edited)

        edited2 = _pics()
        edited2[0].mtime = 5000
        assert self._key(base) != self._key(edited2)

    def test_collection_order_does_not_change_the_key(self):
        """Order varies with the directory cache; it must not cause a spurious miss."""
        base = _pics()
        assert self._key(base) == self._key(list(reversed(base)))


class TestSafety:
    def test_a_match_naming_an_unknown_file_is_refused_wholesale(self, cache):
        """Not a partial list: a missing path means the stored set cannot be trusted.

        The key already covers the file set, so this should be unreachable -- but a hash
        collision or a corrupted row should cost a rescan, not a wrong answer.
        """
        pics = _pics()
        key = compute_key(pics, 75, False, False)
        cache.put(key, _matches(pics))
        assert cache.get(key, pics[:1]) is None

    def test_put_replaces_rather_than_accumulates(self, cache):
        """Only the newest key is worth keeping; older ones can never be hit again."""
        pics = _pics()
        cache.put(compute_key(pics, 75, False, False), _matches(pics))
        cache.put(compute_key(pics, 80, False, False), _matches(pics)[:1])
        assert cache.get(compute_key(pics, 75, False, False), pics) is None
        assert len(cache.get(compute_key(pics, 80, False, False), pics)) == 1

    def test_schema_bump_discards_rather_than_migrates(self, tmp_path):
        from core.pe import match_cache

        db = tmp_path / "m.db"
        c = MatchCache()
        c.connect(db)
        pics = _pics()
        key = compute_key(pics, 75, False, False)
        c.put(key, _matches(pics))
        c.close()

        original = match_cache.SCHEMA_VERSION
        match_cache.SCHEMA_VERSION = original + 1
        try:
            c2 = MatchCache()
            c2.connect(db)
            assert c2.get(key, pics) is None
            c2.close()
        finally:
            match_cache.SCHEMA_VERSION = original

    def test_default_path_sits_beside_the_other_caches(self, tmp_path):
        path = default_cache_path(str(tmp_path))
        assert path.startswith(str(tmp_path))
        assert path.endswith("picture_matches.db")


class TestGetmatchesIntegration:
    """The wiring. A cache nothing consults is an elaborate no-op."""

    @staticmethod
    def _real_pictures(tmp_path, n=6):
        """Identical small images, so matching definitely produces matches."""
        pytest.importorskip("qtpy", reason="matching decodes through a Qt binding")
        import core.pe.photo
        from qt.pe.photo import File as PlatPhoto

        core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = PlatPhoto
        from core.tests.cli_test import _bmp

        pics = []
        for i in range(n):
            path = tmp_path / f"img{i}.bmp"
            path.write_bytes(_bmp(colour=(0x10 * (i // 2), 0x20, 0x30)))
            pic = PlatPhoto(path)
            pic._read_info("dimensions")
            pic.is_ref = False
            pics.append(pic)
        return pics

    def test_second_call_is_served_from_the_cache(self, tmp_path, cache):
        """Proven by counting real work, not by timing: prepare_pictures must not run again."""
        from core.pe import matchblock

        pics = self._real_pictures(tmp_path)
        block_db = str(tmp_path / "blocks.db")

        first = matchblock.getmatches(pics, cache_path=block_db, threshold=75, match_cache=cache)

        calls = {"n": 0}
        original = matchblock.prepare_pictures

        def counting(*a, **k):
            calls["n"] += 1
            return original(*a, **k)

        matchblock.prepare_pictures = counting
        try:
            second = matchblock.getmatches(pics, cache_path=block_db, threshold=75, match_cache=cache)
        finally:
            matchblock.prepare_pictures = original

        assert calls["n"] == 0, "the second call recomputed instead of using the cache"
        assert len(second) == len(first)

    def test_changing_the_threshold_recomputes(self, tmp_path, cache):
        from core.pe import matchblock

        pics = self._real_pictures(tmp_path)
        block_db = str(tmp_path / "blocks.db")
        matchblock.getmatches(pics, cache_path=block_db, threshold=75, match_cache=cache)

        calls = {"n": 0}
        original = matchblock.prepare_pictures

        def counting(*a, **k):
            calls["n"] += 1
            return original(*a, **k)

        matchblock.prepare_pictures = counting
        try:
            matchblock.getmatches(pics, cache_path=block_db, threshold=95, match_cache=cache)
        finally:
            matchblock.prepare_pictures = original

        assert calls["n"] == 1, "a different threshold must not be served from the cache"

    def test_no_cache_argument_behaves_exactly_as_before(self, tmp_path):
        """The default path must be untouched for callers that pass nothing."""
        from core.pe import matchblock

        pics = self._real_pictures(tmp_path)
        block_db = str(tmp_path / "blocks.db")
        a = matchblock.getmatches(pics, cache_path=block_db, threshold=75)
        b = matchblock.getmatches(pics, cache_path=block_db, threshold=75)
        assert len(a) == len(b)
