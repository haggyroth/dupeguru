"""BK-tree for metric-space nearest-neighbour search.

Used by the photo engine (core/pe/matchblock.py) to reduce O(n²) block-signature
comparisons to O(n · log n) average case when the similarity threshold is tight
(most photo pairs are far apart in the metric, so the tree prunes almost everything).

Distance function
-----------------
avgdiff(a, b, limit, min_iter) from core.pe._block (C extension) computes the
average per-block absolute RGB-sum difference.  It triggers early termination when
the running partial average exceeds `limit` (returns limit+1 without completing).

For BK-tree *node distances* we must have the TRUE distance so the triangle-
inequality pruning is correct.  We therefore call avgdiff with limit=769, which is
above the maximum possible avgdiff value (3 × 255 = 765), so early termination
never fires and the full distance is always computed.

The same distance function is also used for BK-tree *queries*.  Results from
find() are already within the requested max_dist, so no second verification pass
is needed.
"""

from __future__ import annotations

try:
    from core.pe.block import avgdiff, DifferentBlockCountError, NoBlocksError

    _MAX_LIMIT = 769  # safely above max possible avgdiff (3 × 255 = 765)

    def _dist(a: list, b: list) -> int:
        """True, symmetric distance between two equal-length block lists.

        Returns _MAX_LIMIT for incompatible inputs (empty or mismatched length)
        so they are treated as maximally distant and never matched.
        """
        try:
            return avgdiff(a, b, _MAX_LIMIT, 1)
        except (DifferentBlockCountError, NoBlocksError):
            return _MAX_LIMIT

except ImportError:
    # C extension not compiled — provide a stub so the module can be imported
    # even in environments without the build artefact (e.g. CI without build.py).
    # getmatches() will raise ImportError at call time if it tries to use the tree.
    def _dist(a, b):  # type: ignore[misc]
        raise RuntimeError("core.pe._block C extension is not available")


class BKTree:
    """A single node of a BK-tree.

    Build the tree by creating a root node and calling ``insert`` for every
    subsequent item.  Query with ``find`` to retrieve all items within a given
    distance of a query vector.

    Attributes
    ----------
    key : any
        Opaque caller-supplied identifier (e.g. an integer cache_id).
    blocks : list[tuple[int, int, int]]
        The block-signature vector for this node.
    children : dict[int, BKTree]
        Child nodes keyed by the integer distance from *this* node to the child.
    """

    __slots__ = ("key", "blocks", "children")

    def __init__(self, key, blocks: list) -> None:
        self.key = key
        self.blocks = blocks
        self.children: dict[int, BKTree] = {}

    def insert(self, key, blocks: list) -> None:
        """Insert ``(key, blocks)`` into the subtree rooted at this node."""
        d = _dist(self.blocks, blocks)
        if d in self.children:
            self.children[d].insert(key, blocks)
        else:
            self.children[d] = BKTree(key, blocks)

    def find(self, query_blocks: list, max_dist: int) -> list[tuple]:
        """Return ``[(key, distance), ...]`` for all items within *max_dist*.

        Uses the BK-tree triangle-inequality pruning: for a node whose edge
        weight from its parent is *c*, and the distance from the query to the
        parent is *d*, the node's subtree can only contain results if
        ``d - max_dist ≤ c ≤ d + max_dist``.
        """
        d = _dist(self.blocks, query_blocks)
        results: list[tuple] = []
        if d <= max_dist:
            results.append((self.key, d))
        lo = d - max_dist
        hi = d + max_dist
        for c_dist, child in self.children.items():
            if lo <= c_dist <= hi:
                results.extend(child.find(query_blocks, max_dist))
        return results
