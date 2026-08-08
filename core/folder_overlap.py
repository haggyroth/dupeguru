# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""How much of each folder's content exists somewhere else (issue #127).

Before deciding anything about several thousand duplicate groups, the useful question is often
structural: *which folders are basically copies of each other?* Someone facing an archive they
have not touched in years wants to know its shape -- which folders are redundant, which are
partially merged, which are unique -- before deleting anything.

**How this differs from the folder rollup (#122).** The rollup answers "which folder pairs
explain these duplicates, and can I act on one" -- it deliberately hides anything below a
confidence threshold, because acting on a coincidence is worse than not acting. This answers
"how redundant is this folder", reports the whole spectrum including weak overlaps, and offers
no actions at all. The numbers differ too, and the difference is the point:

* the rollup's *share* is a fraction of the deletable duplicates under a folder
* redundancy here is a fraction of **everything the folder contains**, duplicated or not

A folder holding 1,000 files of which 10 are duplicated reads as a confident pair in the
rollup, because all 10 resolve to the same place, and as 1% redundant here. Both are true and
they answer different questions.

That denominator is why this needs the scan's file counts rather than only its results: a file
with no duplicate never appears in a duplicate group, so results alone cannot say how big a
folder is. The counts are taken while collecting, from a list already in memory.
"""

from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

#: Folders holding fewer files than this are not reported. A folder of two files that happen to
#: be duplicated is "100% redundant" and says nothing about the shape of an archive.
MIN_FILES = 10

#: How many destinations to name per folder. Beyond a few the row stops being readable, and a
#: folder whose content is spread over twenty places is described well enough by the first
#: few plus the total.
MAX_DESTINATIONS = 4


class Destination(NamedTuple):
    """Some of a folder's content also lives here."""

    folder: str
    file_count: int


class FolderOverlap(NamedTuple):
    """What share of one folder's content exists elsewhere, and where."""

    folder: str
    total_files: int  # everything scanned under it, duplicated or not
    duplicated_files: int  # of those, how many also exist somewhere else
    destinations: list  # Destination, biggest first, truncated to MAX_DESTINATIONS
    other_destination_count: int  # destinations beyond those listed

    @property
    def redundancy(self) -> float:
        """Fraction of this folder's content that also exists elsewhere."""
        return self.duplicated_files / self.total_files if self.total_files else 0.0

    @property
    def is_wholly_redundant(self) -> bool:
        """Every file here exists somewhere else.

        Worth naming separately: it is the case where a folder could in principle be removed
        entirely, which is a different statement from "mostly duplicated".
        """
        return self.total_files > 0 and self.duplicated_files == self.total_files


def _is_root(folder: str) -> bool:
    """Whether *folder* is a filesystem root.

    "/ is 22% redundant" is arithmetically true and describes nothing anyone can act on or
    reason about, in the same way "/ duplicates /photos" was meaningless in the rollup.
    """
    return len(Path(folder).parts) <= 1


def count_files_per_folder(files, scan_roots=()) -> Counter:
    """Files under each folder, counted at every ancestor depth within the scanned roots.

    Every depth, so a question about ``/photos`` is answerable as well as one about
    ``/photos/2023``. Built from the list the scan already holds; walking the tree again to
    count would cost another pass over the metadata this exists to save people from.

    Counting stops at the scanned roots, and that is a correctness matter rather than tidiness.
    Scanning ``/Users/k/Downloads`` says nothing about ``/Users``: dupeGuru never looked inside
    the rest of it, so "62% of /Users is duplicated" would be computed against 65 files when
    the folder holds thousands. A percentage whose denominator is only the part we happened to
    look at is not wrong-ish, it is wrong.

    With no roots given, every ancestor is counted -- convenient for tests, and the caller that
    matters always passes them.
    """
    roots = [Path(root) for root in scan_roots]

    def within_scan(folder: Path) -> bool:
        if not roots:
            return True
        return any(folder == root or root in folder.parents for root in roots)

    totals = Counter()
    for file in files:
        for parent in file.path.parents:
            if within_scan(parent):
                totals[str(parent)] += 1
    return totals


def build_overlaps(results, folder_totals, min_files=MIN_FILES) -> list:
    """Describe each folder by how much of its content exists elsewhere.

    *folder_totals* is the mapping from :func:`count_files_per_folder`. Without it there is no
    denominator -- results hold only duplicates, so a folder of 1,000 files with 10 duplicated
    is indistinguishable from a folder of 10.

    Reported for both sides of every relationship: that ``/backup`` is 87% redundant and
    ``/photos`` is 12% redundant are different facts, and which one matters depends on what
    the user is trying to work out.
    """
    # folder -> how many of its files exist elsewhere. Counted once per file rather than once
    # per match: a file duplicated in three places is still one redundant file, and counting
    # its matches would push a folder past 100% redundant.
    duplicated = Counter()
    # folder -> Counter of where its duplicated content also lives
    destinations = defaultdict(Counter)

    for group in results.groups:
        members = list(group)
        for member in members:
            others = [other for other in members if other is not member]
            if not others:
                continue
            for folder in member.path.parents:
                folder = str(folder)
                if _is_root(folder):
                    continue
                duplicated[folder] += 1
                for other in others:
                    for other_folder in other.path.parents:
                        other_folder = str(other_folder)
                        if _is_root(other_folder):
                            continue
                        if other_folder == folder:
                            # The counterpart is inside this same folder, so it says nothing
                            # about content existing *elsewhere*.
                            continue
                        destinations[folder][other_folder] += 1

    overlaps = []
    for folder, total in folder_totals.items():
        if total < min_files or _is_root(folder):
            continue
        duplicated_here = duplicated.get(folder, 0)
        if not duplicated_here:
            continue
        ranked = _most_specific(destinations[folder].most_common())
        shown = [Destination(name, count) for name, count in ranked[:MAX_DESTINATIONS]]
        overlaps.append(
            FolderOverlap(
                folder=folder,
                total_files=total,
                # Clamped: the two numbers come from different passes -- duplicates from the
                # results, the total from the scan's file list -- and a folder reported as more
                # than wholly redundant would sort above everything real.
                duplicated_files=min(duplicated_here, total),
                destinations=shown,
                other_destination_count=max(0, len(ranked) - len(shown)),
            )
        )

    # Most redundant first: the folders someone could most plausibly remove. Ties go to the
    # larger folder, where the same percentage means more files.
    overlaps.sort(key=lambda overlap: (overlap.redundancy, overlap.total_files), reverse=True)
    return _drop_redundant_parents(overlaps)


def _most_specific(ranked):
    """Drop a destination that an equally-matching descendant already covers.

    ``/photos/2023 (87), /photos (87)`` is the same 87 files described at two depths. Only the
    innermost says anything the other does not.
    """
    kept = []
    for folder, count in ranked:
        path = Path(folder)
        covered = any(path in Path(other).parents and other_count == count for other, other_count in ranked)
        if not covered:
            kept.append((folder, count))
    return kept


def _drop_redundant_parents(overlaps):
    """Drop a folder whose parent describes exactly the same content.

    When ``/backup`` holds nothing but ``/backup/2023``, both come out with identical counts
    and listing both says the same thing twice. The outer folder is kept: "/backup is 87%
    redundant" is the statement someone would act on.
    """
    by_folder = {overlap.folder: overlap for overlap in overlaps}
    kept = []
    for overlap in overlaps:
        parent = str(Path(overlap.folder).parent)
        twin = by_folder.get(parent)
        if (
            twin is not None
            and twin.total_files == overlap.total_files
            and twin.duplicated_files == overlap.duplicated_files
        ):
            continue
        kept.append(overlap)
    return kept
