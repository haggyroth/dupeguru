"""Tests for the BK-tree used by the photo matching engine.

Two sections:
  Part A — pure Python tests that use a simple integer distance function.
            No C extension required; these always run.
  Part B — block-based tests that exercise BKTree with the real avgdiff
            distance.  Skipped if core.pe._block has not been compiled.
"""

import random
import pytest

from core.pe.bktree import BKTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _int_dist(a: int, b: int) -> int:
    """Trivial integer metric used in Part A tests."""
    return abs(a - b)


class _IntTree:
    """Thin wrapper around BKTree that injects _int_dist as the distance
    function by monkey-patching the module-level _dist during construction
    and queries.  This avoids touching avgdiff entirely in Part A.
    """

    def __init__(self, key: int, value: int):
        # Store ints as single-element lists so BKTree accepts them,
        # but override the _dist function via the module.
        import core.pe.bktree as bktree_mod
        self._orig_dist = bktree_mod._dist
        bktree_mod._dist = lambda a, b: _int_dist(a[0], b[0])
        self._tree = BKTree(key, [value])
        self._mod = bktree_mod

    def __del__(self):
        self._mod._dist = self._orig_dist

    def insert(self, key: int, value: int) -> None:
        self._tree.insert(key, [value])

    def find(self, value: int, max_dist: int):
        return self._tree.find([value], max_dist)


def _make_int_tree(items: dict[int, int]) -> _IntTree:
    """items: {key: value}"""
    keys = list(items)
    t = _IntTree(keys[0], items[keys[0]])
    for k in keys[1:]:
        t.insert(k, items[k])
    return t


# ---------------------------------------------------------------------------
# Part A — pure Python BK-tree structure and search tests
# ---------------------------------------------------------------------------


class TestBKTreePython:
    def test_insert_and_find_exact(self):
        """Root item is always found at distance 0 from itself."""
        t = _make_int_tree({1: 10})
        results = t.find(10, 0)
        assert len(results) == 1
        assert results[0][0] == 1
        assert results[0][1] == 0

    def test_find_within_range(self):
        """Items within radius are returned; items outside are not."""
        # Values: A=10, B=12 (d=2), C=15 (d=5), D=20 (d=10)
        t = _make_int_tree({1: 10, 2: 12, 3: 15, 4: 20})
        results = t.find(10, 5)
        found_keys = {r[0] for r in results}
        assert 1 in found_keys  # d=0
        assert 2 in found_keys  # d=2
        assert 3 in found_keys  # d=5
        assert 4 not in found_keys  # d=10, outside radius

    def test_find_excludes_all_when_radius_zero_and_not_exact(self):
        """Nothing found when query doesn't match anything exactly."""
        t = _make_int_tree({1: 10, 2: 20, 3: 30})
        results = t.find(15, 0)
        assert results == []

    def test_single_element_tree(self):
        """One-element tree: self found at d=0, nothing else at d=0."""
        t = _make_int_tree({99: 42})
        assert len(t.find(42, 0)) == 1
        assert t.find(99, 0) == []  # different value, not in tree

    def test_no_false_negatives_vs_brute_force(self):
        """BK-tree and brute-force agree on 50 random integers, radius 8."""
        rng = random.Random(12345)
        values = {i: rng.randint(0, 100) for i in range(50)}
        t = _make_int_tree(values)
        query = 42
        radius = 8

        bf = {k for k, v in values.items() if abs(v - query) <= radius}
        bk = {r[0] for r in t.find(query, radius)}
        assert bk == bf

    def test_duplicate_distances(self):
        """Multiple items equidistant from the root are all stored and found."""
        # Root=0, then add values 5, -5, 10, -10 (distances 5, 5, 10, 10)
        t = _make_int_tree({0: 0, 1: 5, 2: -5, 3: 10, 4: -10})
        results = t.find(0, 5)
        found_keys = {r[0] for r in results}
        assert {0, 1, 2} == found_keys  # 0 (d=0), 5 (d=5), -5 (d=5)
        assert 3 not in found_keys
        assert 4 not in found_keys

    def test_distance_returned_correctly(self):
        """find() returns the correct distance alongside each key."""
        t = _make_int_tree({1: 0, 2: 3, 3: 7})
        results = {r[0]: r[1] for r in t.find(0, 10)}
        assert results[1] == 0
        assert results[2] == 3
        assert results[3] == 7

    def test_large_tree_no_false_negatives(self):
        """200-item tree matches brute force for 10 random queries."""
        rng = random.Random(99999)
        values = {i: rng.randint(0, 500) for i in range(200)}
        t = _make_int_tree(values)
        for _ in range(10):
            query = rng.randint(0, 500)
            radius = rng.randint(5, 30)
            bf = {k for k, v in values.items() if abs(v - query) <= radius}
            bk = {r[0] for r in t.find(query, radius)}
            assert bk == bf, f"Mismatch for query={query} radius={radius}"


# ---------------------------------------------------------------------------
# Part B — block-based tests using the real avgdiff C extension
# ---------------------------------------------------------------------------

try:
    from core.pe.block import avgdiff as _avgdiff_check
    _HAS_BLOCK_EXT = True
except ImportError:
    _HAS_BLOCK_EXT = False

_block_skip = pytest.mark.skipif(
    not _HAS_BLOCK_EXT,
    reason="core.pe._block C extension not compiled",
)

BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREY10 = (10, 10, 10)   # avgdiff from BLACK = 30
GREY20 = (20, 20, 20)   # avgdiff from BLACK = 60

_N = 225  # 15 × 15 blocks


@_block_skip
class TestBKTreeWithBlocks:
    def test_identical_blocks_distance_zero(self):
        """Two pictures with the same block signature match at distance 0."""
        blocks = [RED] * _N
        t = BKTree(1, blocks)
        results = t.find(blocks, 0)
        assert len(results) == 1
        assert results[0] == (1, 0)

    def test_different_blocks_excluded_at_tight_threshold(self):
        """Maximally different blocks (dist=255) are not found at radius 20."""
        black_blocks = [BLACK] * _N
        red_blocks = [RED] * _N
        t = BKTree(1, black_blocks)
        results = t.find(red_blocks, 20)
        assert results == []

    def test_near_duplicate_found_within_threshold(self):
        """Slightly different blocks (avg diff ≈ 30) are found at radius 40."""
        black_blocks = [BLACK] * _N
        grey_blocks = [GREY10] * _N  # per-block diff = 30; avg = 30
        t = BKTree(1, black_blocks)
        results = t.find(grey_blocks, 40)
        assert len(results) == 1
        assert results[0][0] == 1
        assert results[0][1] == 30

    def test_brute_force_vs_bktree_with_real_blocks(self):
        """BK-tree agrees with brute force for 30 synthetic block signatures."""
        rng = random.Random(77777)

        def _rand_blocks():
            return [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
                    for _ in range(_N)]

        items = {i: _rand_blocks() for i in range(30)}
        keys = list(items)
        t = BKTree(keys[0], items[keys[0]])
        for k in keys[1:]:
            t.insert(k, items[k])

        from core.pe.bktree import _dist
        query = _rand_blocks()
        radius = 40
        bf = {k for k, v in items.items() if _dist(v, query) <= radius}
        bk = {r[0] for r in t.find(query, radius)}
        assert bk == bf
