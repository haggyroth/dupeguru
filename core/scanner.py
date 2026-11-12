# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import itertools
import logging
import os
import re
import sys
import os.path as op
from collections import namedtuple, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from hscommon.jobprogress import job
from hscommon.util import dedupe, rem_file_ext, get_file_ext
from hscommon.trans import tr

from core import combined_scan, engine
from core.hash_cache import hashcachedb, hash_file_worker

_BATCH_SIZE = 500  # rows written to hashcachedb per transaction
_HASH_WINDOW = 10_000  # files submitted to the pool at once; bounds the parent's bookkeeping
_MAX_WINDOWS_WORKERS = 61  # ProcessPoolExecutor's hard limit on Windows

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


def _cache_key(f):
    """``(size, mtime_ns)`` for the hash cache, or None when the file can't be read.

    Uses what the directory walk already collected. The old code stat()ed every candidate again
    here, which is a real syscall per file on Windows (the walk's stat comes free with the
    listing) and a round trip per file on a network share. A file that carries no nanosecond
    timestamp -- one built from the file-list cache -- still gets stat()ed, because a cached
    float cannot be turned back into the integer the cache was keyed on.
    """
    try:
        mtime_ns = getattr(f, "mtime_ns", None)
        if mtime_ns:
            return f.size, mtime_ns
        st = f.path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


class Scanner:
    def __init__(self):
        self.discarded_file_count = 0
        # Every stage that gave up early during the last scan. Carried out of the scan the same
        # way discarded_file_count is, because the front ends need it for the same reason:
        # a number the results alone cannot tell you (issue #180).
        self.scan_report = engine.ScanReport()
        self.parallel_scan = (os.cpu_count() or 1) > 1
        # Populated by _verify_partial_matches when full_verify is on.
        self.discarded_partial_count = 0
        self.verified_partial_count = 0

    def _hash_worker_count(self) -> int:
        """How many processes the hashing pool gets.

        ``hash_workers`` of 0 means automatic: one fewer than the cores. Measured on a spinning
        pool of disks, hashing is bound by per-file access latency rather than bandwidth, so the
        right number depends on the storage and not the CPU -- which is why a person can set it.
        An explicit value is honoured even above the core count (the workers mostly wait on the
        disk), but never above what the platform allows.
        """
        if not self.parallel_scan:
            return 1
        if self.hash_workers > 0:
            workers = self.hash_workers
        else:
            workers = max(1, (os.cpu_count() or 1) - 1)
        if sys.platform == "win32":
            # ProcessPoolExecutor raises ValueError above this on Windows. That used to land in
            # the catch-all below and quietly turn a 64-thread machine into a sequential one.
            workers = min(workers, _MAX_WINDOWS_WORKERS)
        return workers

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
            key = _cache_key(f)
            if key is None:
                continue
            size, mtime_ns = key
            cached = hashcachedb.get(f.path, size, mtime_ns)
            if cached is not None:
                _apply_digest(f, cached, size, bigsize)
            else:
                cache_misses.append((f, size, mtime_ns))

        if not cache_misses:
            j.set_progress(total)
            return

        new_rows: list = []
        workers = self._hash_worker_count()
        miss_total = len(cache_misses)
        half = total // 2

        # Entries that the pool couldn't hash (per-worker exception, or the pool itself died);
        # retried sequentially afterwards.
        failed_entries: list = []
        parallel_done = 0

        window_start = 0
        finished: set = set()  # ids of the current window's files whose futures came back
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                # Submit a window at a time rather than every miss at once. A Future plus its
                # work item and result slot costs ~2.3 KB in the parent, so one per file meant
                # ~1.8 GB of bookkeeping for 800,000 files before a byte had been hashed.
                for window_start in range(0, miss_total, _HASH_WINDOW):
                    finished = set()
                    window = cache_misses[window_start : window_start + _HASH_WINDOW]
                    future_to_meta = {pool.submit(hash_file_worker, str(f.path)): (f, sz, mt) for f, sz, mt in window}
                    for future in as_completed(future_to_meta):
                        f, sz, mt = future_to_meta[future]
                        finished.add(id(f))
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
            # Pool-level failure: everything from the current window on that has not come back.
            # Ids from earlier windows are not in `finished`, but those windows are before
            # window_start and so are not in this slice.
            failed_entries += [m for m in cache_misses[window_start:] if id(m[0]) not in finished]

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

    def _verify_partial_matches(self, matches, j):
        """Re-compare sampled-hash matches on full content, dropping any that disagree.

        Partial matching confirms a pair on three sampled chunks, so two files of equal
        size that happen to agree at those offsets match without ever being compared in
        full. This re-reads only the files involved in such pairs. A pair whose full
        digests agree loses its ``partial`` flag and is thereafter indistinguishable from
        a normally-confirmed match; a pair that disagrees was a false positive and is
        removed before grouping.

        A file whose digest could not be read yields an empty digest. Two of those must
        not be treated as equal, so an empty digest always fails verification.
        """
        partial_indexes = [i for i, m in enumerate(matches) if m.partial]
        if not partial_indexes:
            return matches
        total = len(partial_indexes)
        logging.info("Full-verifying %d partial match(es)", total)
        verified = list(matches)
        discarded = 0
        for done, i in enumerate(partial_indexes, 1):
            m = matches[i]
            first_digest = m.first.digest
            if first_digest and first_digest == m.second.digest:
                # Re-wrapped to clear `partial`; the kind is carried over rather than
                # defaulted, or a verified content match would come out as metadata.
                verified[i] = engine.Match(m.first, m.second, m.percentage, kind=m.kind)
            else:
                verified[i] = None
                discarded += 1
            if done % 10 == 0 or done == total:
                j.set_progress(
                    done * 100 // total,
                    tr("Verifying partial matches %d/%d") % (done, total),
                )
        if discarded:
            logging.info("Full verification discarded %d false positive(s)", discarded)
        self.discarded_partial_count = discarded
        self.verified_partial_count = total - discarded
        return [m for m in verified if m is not None]

    def _split_class(self, members, ignore_list):
        """A content class reduced to the sub-groups that survive the scan's filters.

        Returns ``(groups, fallback)``. Members that still form a complete set of identical
        files come back as lists ready for :meth:`Group.from_identical`; anything a filter has
        punched a hole in comes back as *fallback*, to be expanded into pairs and grouped the
        old way.

        The distinction is not an optimisation detail, it is correctness. A group built from a
        class asserts that every member matches every other, and two of these filters can make
        that false:

        - **The ignore list** removes individual pairs, so the remainder need not be a clique at
          all. Working out the maximal cliques of what is left is exactly what ``get_groups``
          already does, so those classes go back to it.
        - **Two reference files** are never matched to each other -- the matcher skips the pair
          to avoid pairing two files the user protected -- so a class holding more than one is
          likewise not a clique.

        The other two filters split cleanly and keep the guarantee: dropping files that no longer
        exist leaves the rest identical to each other, and splitting by extension leaves each
        part identical within itself.
        """
        if self.include_exists_check:
            members = [f for f in members if f.exists()]
        if not self.mix_file_kind:
            by_ext = defaultdict(list)
            for f in members:
                by_ext[get_file_ext(f.name)].append(f)
            parts = list(by_ext.values())
        else:
            parts = [members]

        groups, fallback = [], []
        for part in parts:
            if len(part) < 2:
                continue
            refs = sum(1 for f in part if f.is_ref)
            ignored = ignore_list and any(
                ignore_list.are_ignored(str(a.path), str(b.path)) for a, b in itertools.combinations(part, 2)
            )
            if refs > 1 or ignored:
                fallback.append(part)
            else:
                groups.append(part)
        return groups, fallback

    def _prepare_content_candidates(self, files, j):
        """Everything a contents scan does before comparing anything.

        Shared by the pairwise and equivalence-class routes so they cannot diverge. Skipping it
        was not a subtle mistake: it silently dropped the size thresholds, the job budget split,
        and the parallel hash pre-population, so a second scan stopped using the hash cache.

        Returns the filtered files and the sub-job to report progress against.
        """
        j = j.start_subjob([2, 8])
        if self.size_threshold:
            files = [f for f in files if f.size >= self.size_threshold]
        if self.large_size_threshold:
            files = [f for f in files if f.size <= self.large_size_threshold]
        if hashcachedb.conn is not None:
            # Size-first pre-filter: only hash files that share a size with a partner.
            size_groups: dict = defaultdict(list)
            for f in files:
                size_groups[f.size].append(f)
            candidates = [f for grp in size_groups.values() if len(grp) > 1 for f in grp]
            if candidates:
                j.start_job(len(candidates), tr("Checking hash cache"))
                self._hash_files_parallel(candidates, j, bigsize=self.big_file_size_threshold)
        return files, j

    def _getclasses(self, files, j):
        """Equivalence classes for a contents scan, after the same preparation as _getmatches."""
        files, j = self._prepare_content_candidates(files, j)
        return engine.content_classes(files, bigsize=self.big_file_size_threshold, j=j, report=self.scan_report)

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
            return engine.getmatches_by_contents(
                files, bigsize=self.big_file_size_threshold, j=j, report=self.scan_report
            )  # noqa: E501
        else:
            j = j.start_subjob([2, 8])
            kw = {}
            kw["match_similar_words"] = self.match_similar_words
            kw["weight_words"] = self.word_weighting
            kw["min_match_percentage"] = self.min_match_percentage
            # FIELDSNOORDER is FIELDS plus a flag. Resolve it into a local rather than
            # writing back to self.scan_type: that rewrite was permanent, so a second
            # get_dupe_groups() call on the same Scanner silently did an *ordered* field
            # comparison, and it made the FIELDSNOORDER branch further down unreachable.
            scan_type = self.scan_type
            if scan_type == ScanType.FIELDSNOORDER:
                scan_type = ScanType.FIELDS
                kw["no_field_order"] = True
            func = {
                ScanType.FILENAME: lambda f: engine.getwords(rem_file_ext(f.name)),
                ScanType.FIELDS: lambda f: engine.getfields(rem_file_ext(f.name)),
                ScanType.TAG: lambda f: [
                    engine.getwords(str(getattr(f, attrname)))
                    for attrname in SCANNABLE_TAGS
                    if attrname in self.scanned_tags
                ],
            }[scan_type]
            for f in j.iter_with_progress(files, tr("Read metadata of %d/%d files")):
                logging.debug("Reading metadata of %s", f.path)
                f.words = func(f)
            return engine.getmatches(files, j=j, report=self.scan_report, **kw)

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
        combining = self.combine_picture_matching and self.scan_type == ScanType.CONTENTS
        if combining:
            # Two matchers means two phases, and a Job hands out its budget once. Splitting it
            # here gives each phase its own allowance -- without this the picture matcher's own
            # start_subjob raises JobCountError, because content matching already spent it.
            # Weighted toward pictures: decoding every image and comparing near-quadratically
            # dwarfs a warm content scan, so an even split would show a bar that races to half
            # and then appears to hang.
            match_job = j.start_subjob([2, 8], tr("Looking for duplicate contents"))
        else:
            match_job = j
        # A plain contents scan can build its groups from equivalence classes, which is linear in
        # each cluster where the pairwise path is quadratic (issue #180). The other scan types
        # keep the pairwise route: picture matching merges a second match list into this one,
        # full verification filters matches, and folder mode has a match-based filter of its own.
        use_classes = self.scan_type == ScanType.CONTENTS and not combining and not self.full_verify
        intact_classes = None
        if use_classes:
            classes = self._getclasses(files, match_job)
            logging.info("Found %d content class(es)" % len(classes))
            intact_classes = []
            broken = []
            for members, partial in classes:
                intact, split = self._split_class(members, ignore_list)
                intact_classes += [(part, partial) for part in intact]
                broken += [engine.ContentClass(part, partial) for part in split]
            # Anything a filter broke becomes ordinary matches and takes the ordinary route from
            # here, so it meets every filter below exactly as it always did. Only the classes
            # that are still complete skip the pairwise machinery.
            matches = engine.matches_from_classes(broken) if broken else []
            if broken:
                logging.info("Falling back to pairwise grouping for %d class(es)" % len(broken))
        else:
            matches = self._getmatches(files, match_job)
            logging.info("Found %d matches" % len(matches))
        if combining:
            # Only alongside a contents scan. Merging resemblances into a filename or tag scan
            # would mix two weak signals and call the result a duplicate.
            picture_matches = combined_scan.picture_matches_over(
                files,
                match_job,
                threshold=self.min_match_percentage,
                match_scaled=self.match_scaled,
                match_rotated=self.match_rotated,
                cache_path=self.cache_path,
            )
            matches = combined_scan.merge_matches(matches, picture_matches)
            logging.info("Found %d matches after picture matching" % len(matches))
        if self.full_verify:
            # Before any grouping, so a discarded false positive can't drag unrelated
            # files into a group with it.
            matches = self._verify_partial_matches(matches, j)
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
        groups = engine.get_groups(matches, report=self.scan_report)
        if intact_classes:
            groups += [
                engine.Group.from_identical(part, 100, partial, engine.MatchKind.EXACT)
                for part, partial in intact_classes
            ]
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
    #: Processes hashing file contents in parallel. 0 picks automatically (cores minus one).
    hash_workers = 0
    full_verify = False
    #: Also look for visually similar images during a standard scan. Off by default: picture
    #: matching decodes every image and compares near-quadratically, measured at 15.9s against
    #: 0.1s for the same file count, so it must be asked for. See core/combined_scan.py.
    combine_picture_matching = False
    #: Picture-matching knobs, declared on the base class rather than only on ScannerPE so a
    #: standard scan combining picture matching can honour them. DupeGuru.start_scanning copies
    #: an option onto the scanner only when it already has the attribute, so an option declared
    #: only on the picture scanner is silently dropped in standard mode.
    match_scaled = False
    match_rotated = False
    #: Where block signatures are cached. Declared here, not only on ScannerPE, because a
    #: standard scan that also matches pictures needs somewhere to put them -- and reusing the
    #: same cache means a later picture-mode scan does not recompute what this one worked out.
    cache_path = None
    word_weighting = False
    include_exists_check = True
