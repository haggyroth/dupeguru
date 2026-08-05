# Created By: Virgil Dupras
# Created On: 2007/02/25
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from collections import defaultdict

from hscommon.trans import tr
from hscommon.jobprogress import job

from core.engine import Match
from core.pe.bktree import BKTree
from core.pe.cache import colors_to_bytes
from core.pe.cache_sqlite import SqliteCache

MIN_ITERATIONS = 3
BLOCK_COUNT_PER_SIDE = 15


def get_cache(cache_path, readonly=False):
    return SqliteCache(cache_path, readonly=readonly)


# Below this many pictures a process pool costs more than it saves. Starting workers and
# importing Qt in each of them measured ~1.3s on a 12-core machine, against ~7ms to prepare
# one picture serially, so the pool only pays for itself past a couple of hundred pictures.
# Measured rather than guessed: at 240 pictures an unchunked pool was five times *slower*
# than the serial path.
PARALLEL_THRESHOLD = 250

# Rows per cache transaction while preparing.
_CACHE_WRITE_BATCH = 500


def _parallel_enabled(count):
    """Whether a process pool is worth it for *count* pictures."""
    return (os.cpu_count() or 1) > 1 and count >= PARALLEL_THRESHOLD


def _prepare_parallel(pictures, cache, with_dimensions, match_rotated, prepared, j):
    """Prepare *pictures* through a process pool. Returns those the pool did not finish.

    Anything the pool could not handle -- a failed worker, or a pool that could not start at
    all -- is returned for the sequential path rather than dropped. A picture that fails to
    decode is not returned: that is a real error about the file, and repeating it serially
    would only produce the same failure more slowly.
    """
    photo_class = type(pictures[0])
    module_name, class_name = photo_class.__module__, photo_class.__name__
    args = [(p.unicode_path, module_name, class_name, with_dimensions, match_rotated) for p in pictures]
    by_path = {p.unicode_path: p for p in pictures}
    workers = max(1, (os.cpu_count() or 1) - 1)
    done_paths = set()
    pending = []

    j.start_job(len(pictures), tr("Analyzed %d/%d pictures") % (0, len(pictures)))
    completed = 0
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            # chunksize matters more than it looks: at the default of 1 the per-task round
            # trip dominates work that only takes a few milliseconds, which made the pool
            # slower than the serial path outright. Batching amortises it.
            chunksize = max(1, len(args) // (workers * 8))
            for path_str, blocks, dimensions, error in pool.map(prepare_picture_worker, args, chunksize=chunksize):
                completed += 1
                done_paths.add(path_str)
                picture = by_path[path_str]
                if error is not None:
                    logging.warning("Could not prepare %s: %s", path_str, error)
                    continue
                pending.append((path_str, blocks))
                if dimensions is not None:
                    picture.dimensions = dimensions
                prepared.append(picture)
                # Written in batches: the connection autocommits, so one write per picture
                # is one transaction per picture, which is what limited this once the
                # decoding itself was parallel.
                if len(pending) >= _CACHE_WRITE_BATCH:
                    cache.set_blocks_raw_many(pending)
                    pending.clear()
                if completed % 100 == 0:
                    j.set_progress(completed, tr("Analyzed %d/%d pictures") % (completed, len(pictures)))
            if pending:
                cache.set_blocks_raw_many(pending)
                pending.clear()
    except Exception as exc:
        logging.warning("Parallel picture preparation failed (%s), falling back to sequential", exc)

    return [p for p in pictures if p.unicode_path not in done_paths]


def prepare_picture_worker(args):
    """Decode one picture and return its encoded block signatures.

    Module level so ProcessPoolExecutor can pickle it, and it takes only plain data for the
    same reason: the concrete Photo subclass is chosen at runtime by the front end
    (PLAT_SPECIFIC_PHOTO_CLASS), which a spawned child never sees, so the class is passed by
    module and name and imported here.

    Returns encoded bytes rather than lists of tuples. That is not only about memory: these
    cross a process boundary, and a 15x15 signature pickles as ~700 bytes encoded against
    ~37 KB inflated, so it decides what the IPC costs as well.
    """
    path_str, module_name, class_name, with_dimensions, match_rotated = args
    try:
        import importlib

        photo_class = getattr(importlib.import_module(module_name), class_name)
        picture = photo_class(Path(path_str))
        dimensions = picture.dimensions if with_dimensions else None
        if match_rotated:
            blocks = [colors_to_bytes(picture.get_blocks(BLOCK_COUNT_PER_SIDE, o)) for o in range(1, 9)]
        else:
            blocks = [b""] * 8
            index = max(picture.get_orientation() - 1, 0)
            blocks[index] = colors_to_bytes(picture.get_blocks(BLOCK_COUNT_PER_SIDE))
        return (path_str, blocks, dimensions, None)
    except MemoryError:
        # Reported rather than raised: one oversized picture should not abandon the scan.
        return (path_str, None, None, "MemoryError")
    except Exception as e:
        return (path_str, None, None, f"{type(e).__name__}: {e}")


def prepare_pictures(pictures, cache_path, with_dimensions, match_rotated, j=job.nulljob):
    # The MemoryError handlers in there use logging without first caring about whether or not
    # there is enough memory left to carry on the operation because it is assumed that the
    # MemoryError happens when trying to read an image file, which is freed from memory by the
    # time that MemoryError is raised.
    cache = get_cache(cache_path)
    cache.purge_outdated()
    prepared = []  # only pictures for which there was no error getting blocks

    # Decoding and hashing images is the dominant cost of a first scan and is entirely
    # CPU-bound, so it goes through a process pool exactly as content-scan hashing does in
    # core.scanner. Cache reads and writes stay on this side: SQLite connections are not
    # shared across processes, and the workers deliberately know nothing about the cache.
    todo = []
    for picture in pictures:
        if not picture.path:
            logging.warning("We have a picture with a null path here")
            continue
        try:
            needs_work = picture.unicode_path not in cache or (
                match_rotated and any(not block for block in cache.get_blocks_raw(picture.unicode_path))
            )
        except Exception:
            needs_work = True
        if needs_work:
            todo.append(picture)
        else:
            if with_dimensions:
                picture.dimensions
            prepared.append(picture)

    if _parallel_enabled(len(todo)):
        remaining = _prepare_parallel(todo, cache, with_dimensions, match_rotated, prepared, j)
    else:
        remaining = todo

    if not remaining:
        cache.close()
        return prepared

    # Sequential path: everything the pool could not do, plus the whole set when the pool is
    # unavailable or failed. Unchanged behaviour, and the only path before this.
    try:
        for picture in j.iter_with_progress(remaining, tr("Analyzed %d/%d pictures")):
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
                    # Raw, so this emptiness check does not inflate eight signatures per
                    # picture just to look at their length.
                    match_rotated
                    and any(not block for block in cache.get_blocks_raw(picture.unicode_path))
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
    # One pass over the table rather than a get_id and a get_blocks_raw per picture, which
    # is two round trips each -- a million of them for a 500,000-picture scan.
    # Raw bytes rather than inflated tuples: this dict holds one entry per picture for the
    # whole corpus, so the representation decides whether a large scan fits in memory.
    # avgdiff compares bytes directly.
    pic_to_blocks = {}  # picture -> [blocks_0, ..., blocks_7]
    cached = cache.get_blocks_raw_for_paths(p.unicode_path for p in pictures)
    for picture in pictures:
        entry = cached.get(picture.unicode_path)
        if entry is None:
            continue
        picture.cache_id, pic_to_blocks[picture] = entry
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
                    logging.warning("BKTree.find failed for %s orient %d: %s", p.unicode_path, orientation, exc)
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
        ref.dimensions  # pre-read for display in results table
        other.dimensions
        result.append(get_match(ref, other, pct))

    return result
