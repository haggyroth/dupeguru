# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import logging
import os
import re
import os.path as op
from collections import namedtuple, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from hscommon.jobprogress import job
from hscommon.util import dedupe, rem_file_ext, get_file_ext
from hscommon.trans import tr

from core import engine
from core.hash_cache import hashcachedb, hash_file_worker

_BATCH_SIZE = 500  # rows written to hashcachedb per transaction

# It's quite ugly to have scan types from all editions all put in the same class, but because there's
# there will be some nasty bugs popping up (ScanType is used in core when in should exclusively be
# used in core_*). One day I'll clean this up.


class ScanType:
    FILENAME = 0
    FIELDS = 1
    FIELDSNOORDER = 2
    TAG = 3
    FOLDERS = 4
    CONTENTS = 5

    # PE
    FUZZYBLOCK = 10
    EXIFTIMESTAMP = 11


ScanOption = namedtuple("ScanOption", "scan_type label")

SCANNABLE_TAGS = ["track", "artist", "album", "title", "genre", "year"]

RE_DIGIT_ENDING = re.compile(r"\d+|\(\d+\)|\[\d+\]|{\d+}")


def is_same_with_digit(name, refname):
    # Returns True if name is the same as refname, but with digits (with brackets or not) at the end
    if not name.startswith(refname):
        return False
    end = name[len(refname) :].strip()
    return RE_DIGIT_ENDING.match(end) is not None


def remove_dupe_paths(files):
    # Returns files with duplicates-by-path removed. Files with the exact same path are considered
    # duplicates and only the first file to have a path is kept. In certain cases, we have files
    # that have the same path, but not with the same case, that's why we normalize. However, we also
    # have case-sensitive filesystems, and in those, we don't want to falsely remove duplicates,
    # that's why we have a `samefile` mechanism.
    result = []
    path2file = {}
    for f in files:
        normalized = str(f.path).lower()
        if normalized in path2file:
            try:
                if op.samefile(str(f.path), str(path2file[normalized].path)):
                    continue  # same file, it's a dupe
                else:
                    pass  # We don't treat them as dupes
            except OSError:
                continue  # File doesn't exist? Well, treat them as dupes
        else:
            path2file[normalized] = f
        result.append(f)
    return result


def _apply_digest(f, digest, size, bigsize):
    """Set digest fields on a File from a pre-computed full hash.

    For big files (size > bigsize > 0), only f.digest is set so that
    digest_partial and digest_samples are computed lazily with the correct
    partial/sampling algorithms, preserving the big_file_size_threshold
    optimisation. For small files all three fields equal the full hash anyway.
    """
    f.digest = digest
    if bigsize == 0 or size <= bigsize:
        f.digest_partial = digest
        f.digest_samples = digest


class Scanner:
    def __init__(self):
        self.discarded_file_count = 0
        self.parallel_scan = (os.cpu_count() or 1) > 1

    def _hash_files_parallel(self, candidates, j, bigsize=0):
        """Pre-populate File.digest for content-scan candidates using the hash cache
        and a ProcessPoolExecutor for cache misses.

        candidates: list of File objects that share a size with at least one other file.
        Modifies each file's .digest in-place and writes new hashes to hashcachedb.
        j must already have had start_job() called by the caller.
        bigsize: value of big_file_size_threshold — files larger than this need partial/samples
        hashes computed separately, so only f.digest is set for them here.
        """
        total = len(candidates)
        cache_misses = []

        for i, f in enumerate(candidates):
            if i % _BATCH_SIZE == 0:
                j.set_progress(i, tr("Checking hash cache %d/%d") % (i, total))
            try:
                stat = f.path.stat()
                size, mtime_ns = stat.st_size, stat.st_mtime_ns
            except OSError:
                continue
            cached = hashcachedb.get(f.path, size, mtime_ns)
            if cached is not None:
                _apply_digest(f, cached, size, bigsize)
            else:
                cache_misses.append((f, size, mtime_ns))

        if not cache_misses:
            j.set_progress(total)
            return

        new_rows: list = []
        workers = max(1, (os.cpu_count() or 1) - 1) if self.parallel_scan else 1
        miss_total = len(cache_misses)
        half = total // 2

        # Paths that the parallel pool successfully hashed; used to skip them in fallback.
        completed_paths: set[str] = set()
        # Entries that the pool couldn't hash (per-worker exception); retried sequentially.
        failed_entries: list = []
        parallel_done = 0

        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                future_to_meta = {
                    pool.submit(hash_file_worker, str(f.path)): (f, sz, mt)
                    for f, sz, mt in cache_misses
                }
                for future in as_completed(future_to_meta):
                    f, sz, mt = future_to_meta[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logging.warning("Worker failed for %s (%s), will retry", f.path, exc)
                        failed_entries.append((f, sz, mt))
                        parallel_done += 1
                        continue
                    if result is not None:
                        _, digest = result
                        _apply_digest(f, digest, sz, bigsize)
                        new_rows.append((f.path, sz, mt, digest))
                        completed_paths.add(str(f.path))
                    parallel_done += 1
                    if parallel_done % _BATCH_SIZE == 0:
                        hashcachedb.set_batch(new_rows)
                        new_rows = []
                        j.set_progress(
                            half + parallel_done * half // miss_total,
                            tr("Hashing files %d/%d") % (parallel_done, miss_total),
                        )
        except Exception as exc:
            logging.warning("Parallel hashing pool failed (%s), falling back to sequential", exc)
            # Pool-level failure: queue every cache miss not already finished.
            failed_entries = [
                (f, sz, mt) for f, sz, mt in cache_misses if str(f.path) not in completed_paths
            ]

        if failed_entries:
            for seq_done, (f, sz, mt) in enumerate(failed_entries, 1):
                result = hash_file_worker(str(f.path))
                if result is not None:
                    _, digest = result
                    _apply_digest(f, digest, sz, bigsize)
                    new_rows.append((f.path, sz, mt, digest))
                if seq_done % _BATCH_SIZE == 0:
                    hashcachedb.set_batch(new_rows)
                    new_rows = []
                    j.set_progress(
                        half + (parallel_done + seq_done) * half // miss_total,
                        tr("Hashing files %d/%d") % (parallel_done + seq_done, miss_total),
                    )

        if new_rows:
            hashcachedb.set_batch(new_rows)
        j.set_progress(total)

    def _getmatches(self, files, j):
        if (
            self.size_threshold
            or self.large_size_threshold
            or self.scan_type
            in {
                ScanType.CONTENTS,
                ScanType.FOLDERS,
            }
        ):
            j = j.start_subjob([2, 8])
            if self.size_threshold:
                files = [f for f in files if f.size >= self.size_threshold]
            if self.large_size_threshold:
                files = [f for f in files if f.size <= self.large_size_threshold]
        if self.scan_type in {ScanType.CONTENTS, ScanType.FOLDERS}:
            if hashcachedb.conn is not None:
                # Size-first pre-filter: only hash files that share a size with a partner.
                size_groups: dict = defaultdict(list)
                for f in files:
                    size_groups[f.size].append(f)
                candidates = [f for grp in size_groups.values() if len(grp) > 1 for f in grp]
                if candidates:
                    j.start_job(len(candidates), tr("Checking hash cache"))
                    self._hash_files_parallel(candidates, j, bigsize=self.big_file_size_threshold)
            return engine.getmatches_by_contents(files, bigsize=self.big_file_size_threshold, j=j)
        else:
            j = j.start_subjob([2, 8])
            kw = {}
            kw["match_similar_words"] = self.match_similar_words
            kw["weight_words"] = self.word_weighting
            kw["min_match_percentage"] = self.min_match_percentage
            if self.scan_type == ScanType.FIELDSNOORDER:
                self.scan_type = ScanType.FIELDS
                kw["no_field_order"] = True
            func = {
                ScanType.FILENAME: lambda f: engine.getwords(rem_file_ext(f.name)),
                ScanType.FIELDS: lambda f: engine.getfields(rem_file_ext(f.name)),
                ScanType.TAG: lambda f: [
                    engine.getwords(str(getattr(f, attrname)))
                    for attrname in SCANNABLE_TAGS
                    if attrname in self.scanned_tags
                ],
            }[self.scan_type]
            for f in j.iter_with_progress(files, tr("Read metadata of %d/%d files")):
                logging.debug("Reading metadata of %s", f.path)
                f.words = func(f)
            return engine.getmatches(files, j=j, **kw)

    @staticmethod
    def _key_func(dupe):
        return -dupe.size

    @staticmethod
    def _tie_breaker(ref, dupe):
        refname = rem_file_ext(ref.name).lower()
        dupename = rem_file_ext(dupe.name).lower()
        if "copy" in dupename:
            return False
        if "copy" in refname:
            return True
        if is_same_with_digit(dupename, refname):
            return False
        if is_same_with_digit(refname, dupename):
            return True
        return len(dupe.path.parts) > len(ref.path.parts)

    @staticmethod
    def get_scan_options():
        """Returns a list of scanning options for this scanner.

        Returns a list of ``ScanOption``.
        """
        raise NotImplementedError()

    def get_dupe_groups(self, files, ignore_list=None, j=job.nulljob):
        for f in (f for f in files if not hasattr(f, "is_ref")):
            f.is_ref = False
        files = remove_dupe_paths(files)
        logging.info("Getting matches. Scan type: %d", self.scan_type)
        matches = self._getmatches(files, j)
        logging.info("Found %d matches" % len(matches))
        j.set_progress(100, tr("Almost done! Fiddling with results..."))
        # In removing what we call here "false matches", we first want to remove, if we scan by
        # folders, we want to remove folder matches for which the parent is also in a match (they're
        # "duplicated duplicates if you will). Then, we also don't want mixed file kinds if the
        # option isn't enabled, we want matches for which both files exist and, lastly, we don't
        # want matches with both files as ref.
        if self.scan_type == ScanType.FOLDERS and matches:
            allpath = {m.first.path for m in matches}
            allpath |= {m.second.path for m in matches}
            sortedpaths = sorted(allpath)
            toremove = set()
            last_parent_path = sortedpaths[0]
            for p in sortedpaths[1:]:
                if last_parent_path in p.parents:
                    toremove.add(p)
                else:
                    last_parent_path = p
            matches = [m for m in matches if m.first.path not in toremove or m.second.path not in toremove]
        if not self.mix_file_kind:
            matches = [m for m in matches if get_file_ext(m.first.name) == get_file_ext(m.second.name)]
        if self.include_exists_check:
            matches = [m for m in matches if m.first.exists() and m.second.exists()]
        # Contents already handles ref checks, other scan types might not catch during scan
        if self.scan_type != ScanType.CONTENTS:
            matches = [m for m in matches if not (m.first.is_ref and m.second.is_ref)]
        if ignore_list:
            matches = [m for m in matches if not ignore_list.are_ignored(str(m.first.path), str(m.second.path))]
        logging.info("Grouping matches")
        groups = engine.get_groups(matches)
        if self.scan_type in {
            ScanType.FILENAME,
            ScanType.FIELDS,
            ScanType.FIELDSNOORDER,
            ScanType.TAG,
        }:
            matched_files = dedupe([m.first for m in matches] + [m.second for m in matches])
            self.discarded_file_count = len(matched_files) - sum(len(g) for g in groups)
        else:
            # Ticket #195
            # To speed up the scan, we don't bother comparing contents of files that are both ref
            # files. However, this messes up "discarded" counting because there's a missing match
            # in cases where we end up with a dupe group anyway (with a non-ref file). Because it's
            # impossible to have discarded matches in exact dupe scans, we simply set it at 0, thus
            # bypassing our tricky problem.
            # Also, although ScanType.FuzzyBlock is not always doing exact comparisons, we also
            # bypass ref comparison, thus messing up with our "discarded" count. So we're
            # effectively disabling the "discarded" feature in PE, but it's better than falsely
            # reporting discarded matches.
            self.discarded_file_count = 0
        groups = [g for g in groups if any(not f.is_ref for f in g)]
        logging.info("Created %d groups" % len(groups))
        for g in groups:
            g.prioritize(self._key_func, self._tie_breaker)
        return groups

    match_similar_words = False
    min_match_percentage = 80
    mix_file_kind = True
    scan_type = ScanType.FILENAME
    scanned_tags = {"artist", "title"}
    size_threshold = 0
    large_size_threshold = 0
    big_file_size_threshold = 0
    word_weighting = False
    include_exists_check = True
