# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Group the duplicate groups by the folder pair that explains them (issue #122).

Scanning one real photo library produced 5,723 duplicate groups. dupeGuru found them
correctly; the work then moved to the human, and nothing told them *why* those groups existed.
Most of the time there is one explanation -- a backup folder shadowing an original -- so 437
separate decisions are really one decision.

This is presentation over results that already exist. No extra I/O, no new matching, and
nothing here changes what a deletion does.

Two properties matter more than the grouping itself.

**The count shown is the count that would be marked.** Only dupes the results consider markable
are counted, because a rollup that offers "437 files" and then marks 400 is lying about what
the deletion will do. That is checked against ``Results`` rather than assumed.

**The pair does not claim which side is the original.** dupeGuru picks a group's reference by
size when the user has not said otherwise, so the direction of a pair is often incidental
rather than meaningful. Where the user *has* said -- by marking a reference folder -- that is
recorded and can be shown; where they have not, the caller is told the direction is only
incidental so it does not draw an arrow that invents an answer.
"""

from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

#: A pair has to cover at least this share of the deletable files coming out of its folder
#: before it counts as explaining them. Below it, the "rollup" is really a coincidence -- two
#: folders that happen to share a few files -- and presenting it as a single decision would
#: invite the user to act on a pattern that is not there.
#:
#: Provisional. The issue asks for this to be derived from a real corpus rather than guessed,
#: which has not been done yet; see test_the_threshold_is_documented_as_provisional.
MIN_SHARE = 0.7

#: ...and it has to explain at least this many files. A pair covering 100% of two files is
#: arithmetically perfect and worth nothing: collapsing it saves one decision.
MIN_FILES = 5


class FolderPair(NamedTuple):
    """Duplicates in one folder whose references live in another."""

    dupe_folder: str  # where the deletable copies are
    ref_folder: str  # where the files they duplicate are
    dupes: list  # the actual dupes, so marking acts on exactly what was counted
    total_bytes: int  # reclaimable by deleting them; references are not counted
    share: float  # of the deletable files under dupe_folder, how many this pair explains
    direction_is_explicit: bool  # whether the user established which side is the original

    @property
    def file_count(self) -> int:
        return len(self.dupes)


class Rollup(NamedTuple):
    """Folder pairs that explain the results, and whatever they do not."""

    pairs: list  # FolderPair, biggest reclaim first
    unexplained: list  # markable dupes no pair accounted for

    @property
    def explained_count(self) -> int:
        return sum(pair.file_count for pair in self.pairs)

    @property
    def decisions_saved(self) -> int:
        """Files collapsed into pairs, minus the pairs themselves.

        The number this feature exists to move: 437 files under one pair is 436 decisions the
        user no longer has to make one at a time.
        """
        return max(0, self.explained_count - len(self.pairs))


def candidate_pairs(dupe_path: Path, ref_path: Path):
    """Folder pairs that could explain this duplicate, innermost first.

    Every ancestor of the duplicate against every ancestor of its reference.
    ``/backup/2023/a.jpg`` duplicating ``/photos/2023/a.jpg`` could be explained by
    ``(/backup/2023, /photos/2023)`` or by ``(/backup, /photos)``; which to report is decided
    later, once it is known how much each covers.

    Deliberately not a lockstep walk up the two paths. That only ever pairs folders at equal
    depth, so ``/Downloads/a.jpg`` duplicating ``/photos/set0/a.jpg`` would never produce
    ``(/Downloads, /photos)`` -- the pair that actually explains it -- and would instead reach
    ``("/", "/photos")``, which claims the filesystem root duplicates a folder.

    A pair where one side contains the other is skipped: a folder does not duplicate its own
    ancestor, and "/" duplicates nothing.
    """
    for dupe_folder in dupe_path.parents:
        for ref_folder in ref_path.parents:
            if dupe_folder == ref_folder:
                continue
            if dupe_folder in ref_folder.parents or ref_folder in dupe_folder.parents:
                continue
            yield str(dupe_folder), str(ref_folder)


def build_rollup(results, is_reference_folder=None) -> Rollup:
    """Explain *results* as folder pairs where possible.

    *is_reference_folder* is an optional ``f(path_str) -> bool`` saying whether the user marked
    a folder as a reference. It is what separates a direction the user established from one
    dupeGuru inferred from file size; without it no pair claims an explicit direction.
    """
    markable = [
        (dupe, group.ref)
        for group in results.groups
        for dupe in group.dupes
        if results.is_markable(dupe) and group.ref is not None
    ]

    by_pair = defaultdict(list)
    for dupe, ref in markable:
        for pair in candidate_pairs(dupe.path, ref.path):
            by_pair[pair].append(dupe)

    # How many deletable files come out of each folder, counted at every depth. This is the
    # denominator of "share": a pair explains a folder only relative to everything that folder
    # contributes, so a backup folder whose files resolve to five different originals should
    # not read as five confident pairs.
    origin_totals = defaultdict(int)
    for dupe, _ref in markable:
        for parent in dupe.path.parents:
            origin_totals[str(parent)] += 1

    qualifying = []
    for (dupe_folder, ref_folder), dupes in by_pair.items():
        total = origin_totals[dupe_folder]
        share = len(dupes) / total if total else 0.0
        if len(dupes) >= MIN_FILES and share >= MIN_SHARE:
            qualifying.append((dupe_folder, ref_folder, dupes, share))

    # Outermost first. /backup duplicating /photos implies /backup/2023 duplicating
    # /photos/2023, and reporting both tells the user the same thing at several depths.
    #
    # Shallowest duplicate folder wins, then the pair explaining the most files, then the
    # shallowest reference folder. That last term is not cosmetic: /backup -> /photos and
    # /backup -> /photos/2023 can cover exactly the same files, and without it which one gets
    # reported depends on dictionary order. The more general statement is both the more useful
    # one and the deterministic one.
    qualifying.sort(key=lambda item: (len(Path(item[0]).parts), -len(item[2]), len(Path(item[1]).parts)))

    claimed = set()
    pairs = []
    for dupe_folder, ref_folder, dupes, share in qualifying:
        remaining = [dupe for dupe in dupes if id(dupe) not in claimed]
        if len(remaining) < MIN_FILES:
            # What is left after an outer pair took its files is not worth its own row.
            continue
        claimed.update(id(dupe) for dupe in remaining)
        pairs.append(
            FolderPair(
                dupe_folder=dupe_folder,
                ref_folder=ref_folder,
                dupes=remaining,
                total_bytes=sum(dupe.size for dupe in remaining),
                share=share,
                direction_is_explicit=bool(is_reference_folder and is_reference_folder(ref_folder)),
            )
        )

    pairs.sort(key=lambda pair: pair.total_bytes, reverse=True)
    unexplained = [dupe for dupe, _ref in markable if id(dupe) not in claimed]
    return Rollup(pairs=pairs, unexplained=unexplained)
