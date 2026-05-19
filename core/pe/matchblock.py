# Created By: Virgil Dupras
# Created On: 2007/02/25
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import multiprocessing
from collections import defaultdict

from hscommon.trans import tr
from hscommon.jobprogress import job

from core.engine import Match
from core.pe.block import NoBlocksError
from core.pe.bktree import BKTree
from core.pe.cache_sqlite import SqliteCache

MIN_ITERATIONS = 3
BLOCK_COUNT_PER_SIDE = 15


def get_cache(cache_path, readonly=False):
    return SqliteCache(cache_path, readonly=readonly)


def prepare_pictures(pictures, cache_path, with_dimensions, match_rotated, j=job.nulljob):
    # The MemoryError handlers in there use logging without first caring about whether or not
    # there is enough memory left to carry on the operation because it is assumed that the
    # MemoryError happens when trying to read an image file, which is freed from memory by the
    # time that MemoryError is raised.
    cache = get_cache(cache_path)
    cache.purge_outdated()
    prepared = []  # only pictures for which there was no error getting blocks
    try:
        for picture in j.iter_with_progress(pictures, tr("Analyzed %d/%d pictures")):
            if not picture.path:
                # XXX Find the root cause of this. I've received reports of crashes where we had
                # "Analyzing picture at " (without a path) in the debug log. It was an iPhoto scan.
                # For now, I'm simply working around the crash by ignoring those, but it would be
                # interesting to know exactly why this happens. I'm suspecting a malformed
                # entry in iPhoto library.
                logging.warning("We have a picture with a null path here")
                continue
            logging.debug("Analyzing picture at %s", picture.unicode_path)
            if with_dimensions:
                picture.dimensions  # pre-read dimensions
            try:
                if picture.unicode_path not in cache or (
                    match_rotated and any(block == [] for block in cache[picture.unicode_path])
                ):
                    if match_rotated:
                        blocks = [picture.get_blocks(BLOCK_COUNT_PER_SIDE, orientation) for orientation in range(1, 9)]
                    else:
                        blocks = [[]] * 8
                        blocks[max(picture.get_orientation() - 1, 0)] = picture.get_blocks(BLOCK_COUNT_PER_SIDE)
                    cache[picture.unicode_path] = blocks
                prepared.append(picture)
            except (OSError, ValueError) as e:
                logging.warning(str(e))
            except MemoryError:
                logging.warning(
                    "Ran out of memory while reading %s of size %d",
                    picture.unicode_path,
                    picture.size,
                )
                if picture.size < 10 * 1024 * 1024:  # We're really running out of memory
                    raise
    except MemoryError:
        logging.warning("Ran out of memory while preparing pictures")
    cache.close()
    return prepared


def get_match(first, second, percentage):
    if percentage < 0:
        percentage = 0
    return Match(first, second, percentage)


def getmatches(pictures, cache_path, threshold, match_scaled=False, match_rotated=False, j=job.nulljob):
    """Return a list of Match objects for pictures whose block signatures are
    similar enough to meet *threshold* (0–100).

    Uses a BK-tree index to prune the O(n²) comparison space to O(n log n)
    average case.  All block-to-block distances are computed with the C-level
    ``avgdiff`` function (limit=769 so early termination is never triggered and
    the true metric distance is always returned).

    match_scaled : if True, skip dimension checks (scaled duplicates allowed).
    match_rotated : if True, compare each picture's 8 rotated block sets
                    against every other picture's orientation-0 blocks.
    """
    j = j.start_subjob([3, 7])
    pictures = prepare_pictures(pictures, cache_path, not match_scaled, match_rotated, j=j)

    j = j.start_subjob([2, 8], tr("Loading picture blocks"))

    # --- Load all block signatures from the SQLite cache ---
    cache = get_cache(cache_path)
    pic_to_blocks = {}  # picture -> [blocks_0, ..., blocks_7]
    for picture in pictures:
        try:
            picture.cache_id = cache.get_id(picture.unicode_path)
            pic_to_blocks[picture] = cache[picture.cache_id]
        except (ValueError, KeyError):
            pass
    cache.close()

    pictures = [p for p in pictures if p in pic_to_blocks]
    id2picture = {p.cache_id: p for p in pictures}

    if len(pictures) < 2:
        return []

    # --- Group pictures by (normalised) dimensions ---
    # Building separate BK-trees per dimension group means we never waste
    # avgdiff calls comparing pictures that can't possibly match.
    # When match_rotated is True, a (W×H) photo can match a (H×W) photo, so
    # we normalise both to (min, max) so they land in the same group.
    def dim_key(p):
        if match_scaled:
            return None  # single global group
        w, h = p.dimensions
        return (min(w, h), max(w, h)) if match_rotated else (w, h)

    dim_groups: dict = defaultdict(list)
    for p in pictures:
        dim_groups[dim_key(p)].append(p)

    limit = 100 - threshold
    orientation_range = 8 if match_rotated else 1

    # pair_best maps (min_cache_id, max_cache_id) -> best percentage so far.
    # This deduplicates pairs found via multiple query orientations.
    pair_best: dict[tuple, int] = {}

    j.start_job(len(pictures), tr("Matching pictures"))

    for group in dim_groups.values():
        if len(group) < 2:
            j.add_progress(len(group))
            continue

        # Build BK-tree from the orientation-0 blocks of every picture in
        # this dimension group.
        tree: BKTree | None = None
        for p in group:
            blocks_0 = pic_to_blocks[p][0]
            if not blocks_0:
                continue  # no blocks for orientation 0; skip this picture
            if tree is None:
                tree = BKTree(p.cache_id, blocks_0)
            else:
                tree.insert(p.cache_id, blocks_0)

        if tree is None:
            j.add_progress(len(group))
            continue

        # Query the tree with each picture using each of its orientations.
        # This replicates the semantics of the old async_compare loop:
        #   avgdiff(ref.blocks[orientation], other.blocks[0], ...)
        # i.e. we compare the query's rotated view against everyone else's
        # canonical (orientation-0) view that is stored in the tree.
        for p in group:
            for orientation in range(orientation_range):
                query_blocks = pic_to_blocks[p][orientation]
                if not query_blocks:
                    continue
                try:
                    candidates = tree.find(query_blocks, limit)
                except Exception as exc:
                    logging.warning("BKTree.find failed for %s orient %d: %s",
                                    p.unicode_path, orientation, exc)
                    continue
                for cand_id, distance in candidates:
                    if cand_id == p.cache_id:
                        continue  # skip self
                    candidate = id2picture.get(cand_id)
                    if candidate is None:
                        continue
                    if p.is_ref and candidate.is_ref:
                        continue  # never match two reference files
                    key = (min(p.cache_id, cand_id), max(p.cache_id, cand_id))
                    pct = 100 - distance
                    if pct == 100 and p.digest != candidate.digest:
                        # Block signatures collide but files differ: cap at 99 %
                        pct = 99
                    if pct >= threshold and pct > pair_best.get(key, 0):
                        pair_best[key] = pct
            j.add_progress()

    # --- Build Match objects from the deduplicated pair_best table ---
    result = []
    for (id1, id2), pct in pair_best.items():
        ref = id2picture[id1]
        other = id2picture[id2]
        ref.dimensions    # pre-read for display in results table
        other.dimensions
        result.append(get_match(ref, other, pct))

    return result


multiprocessing.freeze_support()
