# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""What a deletion would actually do, computed without deleting anything.

This lives in core rather than in a front end because both need it and they must not
disagree. `check_deletable` is already the single source of truth for "would this deletion
happen" -- the deleter raises on whatever it refuses -- and a plan computed by different logic
than the deletion it predicts is worse than no plan at all.

The cost is one stat() per marked file, which is nothing beside the scan that produced the
results.
"""

from typing import NamedTuple

from core.app import DeleteStatus, DupeGuru, check_deletable
from core.clone import can_clone, cloning_is_possible
from core.confidence import Confidence, classify_group
from core.engine import MatchKind
from hscommon.util import format_size


# Why a candidate was refused, in words. Lives here with the plan so the CLI's wording and
# the GUI's cannot describe the same status differently.
DELETE_STATUS_REASON = {
    DeleteStatus.GONE: "file no longer exists",
    DeleteStatus.SYMLINK: "path is a symlink",
    DeleteStatus.UNREADABLE: "could not read file metadata",
    DeleteStatus.CHANGED: "file changed since last scan",
}


class Reclaimable(NamedTuple):
    """Bytes a deletion would actually free, split by how the match was confirmed.

    Reclaimable is NOT group size: the reference stays, so only the duplicates count.
    Getting that wrong overstates the benefit, which is the error that erodes trust in the
    number.

    ``partial_bytes`` is carried separately rather than folded in, because a sampled-hash
    match is a probable duplicate, not a certain one. Counting those bytes in the headline
    would inflate it with space that might not be duplicated at all.
    """

    total_bytes: int  # bytes freed by removing these files, partial matches included
    partial_bytes: int  # of those, confirmed only by a sampled hash

    @property
    def confirmed_bytes(self) -> int:
        """Bytes backed by a full content comparison -- the figure safe to headline."""
        return self.total_bytes - self.partial_bytes


def reclaimable_of(candidates) -> Reclaimable:
    """Total reclaimable bytes over (size, is_partial) pairs.

    One summation for every caller -- the CLI's results serialisation and ndjson emitter, and
    any front end reporting a plan -- so the ordering the user sees and the bytes a deletion
    promises cannot drift apart.
    """
    total = partial = 0
    for size, is_partial in candidates:
        total += size
        if is_partial:
            partial += size
    return Reclaimable(total_bytes=total, partial_bytes=partial)


class DeletionPlan(NamedTuple):
    """What --delete would actually do, computed without deleting anything."""

    groups: int  # groups containing at least one file that would be deleted
    files: int  # files that would actually be deleted
    total_bytes: int  # bytes reclaimed by those files
    partial: int  # of those, confirmed only by a sampled hash
    full_content: int  # of those, confirmed by a full content comparison
    blocked: dict  # DeleteStatus -> count, for candidates that would be refused
    blocked_bytes: int  # bytes those refused files would have freed
    cross_volume: int  # would-delete files on a different volume from their group's ref
    cloneable: int  # of the would-delete files, how many could be replaced by a clone instead
    confidence: dict  # Confidence tier -> count of planned groups sitting in it
    entries: list  # per-group plan, for machine-readable output


#: Read in chunks rather than whole files: a deletion candidate can be any size, and the point
#: is to stop at the first difference, which is usually near the front when there is one.
_COMPARE_CHUNK = 1024 * 1024


def verify_identical(dupe, ref) -> bool:
    """Whether *dupe* and *ref* hold the same bytes, read and compared now.

    The last gap between "almost certainly identical" and "identical". Everything upstream
    reasons about digests, and a digest is a claim about content rather than the content: xxh3
    makes no collision-resistance promise, and the fallback in core/fs.py was md5 until #189,
    where colliding pairs are constructible rather than improbable.

    Stops at the first differing chunk, so the cost is a full read of each file only when they
    really are identical -- which is the case where the answer is needed.

    The size check is an optimisation, not a correctness step: the chunk comparison already
    separates files of different lengths, since the shorter one runs out first. Removing it
    changes no result, only the cost -- without it, comparing a 10 GB file against a 1-byte one
    reads the 10 GB to learn what one stat() already said.

    Any read error is a refusal, not an exception. A file that cannot be read cannot be shown to
    be a duplicate, and the caller's job is to decline to delete it rather than to crash.
    """
    try:
        if dupe.path.stat().st_size != ref.path.stat().st_size:
            return False
        with dupe.path.open("rb") as first, ref.path.open("rb") as second:
            while True:
                a = first.read(_COMPARE_CHUNK)
                b = second.read(_COMPARE_CHUNK)
                if a != b:
                    return False
                if not a:
                    return True
    except OSError:
        return False


def claims_byte_identity(group, dupe) -> bool:
    """Whether this pair was ever asserted to have identical *contents*.

    Verification only means something against that claim. A picture match says two images look
    alike -- a resize or a re-encode scores 100% while the files genuinely differ -- and a
    metadata match says their tags agree. Byte-comparing either would refuse every deletion and
    make the option useless in the modes where it was never relevant.
    """
    match = group.get_match_of(dupe) if group is not None else None
    return getattr(match, "kind", None) == MatchKind.EXACT


def device_of(path) -> int | None:
    """st_dev for *path*, or None if it cannot be read."""
    try:
        return path.stat().st_dev
    except OSError:
        return None


def plan_entry(path, size, mtime, is_partial: bool) -> tuple:
    """Verdict for one candidate file: (status, would_delete, entry dict).

    Shared by the live and saved-results planners so the two cannot drift apart.
    """
    status, _ = check_deletable(path, size, mtime)
    would_delete = status == DeleteStatus.OK
    entry = {
        "path": str(path),
        "size": size,
        "mtime": mtime,
        "would_delete": would_delete,
        "match_confidence": "partial" if is_partial else "full",
    }
    if not would_delete:
        entry["blocked_reason"] = DELETE_STATUS_REASON[status]
    return status, would_delete, entry


def default_clone_probe(dupe, ref) -> bool:
    """Whether *dupe* could be replaced by a clone of *ref* rather than removed.

    Mirrors what the deletion itself requires: identical digests, and a filesystem that can
    clone. Deliberately conservative -- reporting a file as cloneable and then refusing it at
    deletion time would make the preview a liar about the one thing it is for.
    """
    if not cloning_is_possible():
        return False
    dupe_digest = getattr(dupe, "digest", b"")
    ref_digest = getattr(ref, "digest", b"")
    if not dupe_digest or not ref_digest or dupe_digest != ref_digest:
        return False
    try:
        return can_clone(ref.path, dupe.path.parent)
    except OSError:
        return False


def build_plan(app: DupeGuru, clone_probe=None) -> DeletionPlan:
    """Compute what --delete would do, touching nothing.

    Plans whatever is currently marked. Marking is the caller's business: the CLI marks
    everything before asking, the GUI asks about what the user chose.

    Every candidate is re-validated with check_deletable -- the same predicate the deletion
    itself uses -- so the plan reports the files that would be refused instead of assuming
    every marked file is removable. That costs a stat() per marked file, which is nothing
    beside the scan that produced the results.

    *clone_probe*, when given, is asked about each file that would actually be deleted, and the
    ones it accepts are reported as clonable instead. It is optional because answering costs a
    real filesystem test per candidate; when it is None the plan simply never mentions cloning.

    It was accepted and never called until #214, so ``cloneable`` was always 0 -- which made the
    summary line about copy-on-write clones, and the preview's "replaced by a clone of the
    reference", unreachable in every front end.
    """
    files = total_bytes = partial = full_content = blocked_bytes = cross_volume = 0
    cloneable = 0
    blocked: dict = {}
    confidence = {tier: 0 for tier in Confidence.ORDER}
    entries = []
    group_count = 0

    for group in app.results.groups:
        ref_device = None
        ref_device_read = False
        group_entries = []
        group_has_deletion = False

        for dupe in group.dupes:
            if not app.results.is_marked(dupe):
                continue
            match = group.get_match_of(dupe)
            is_partial = bool(getattr(match, "partial", False)) if match else False
            status, would_delete, entry = plan_entry(dupe.path, dupe.size, dupe.mtime, is_partial)

            if would_delete:
                files += 1
                total_bytes += dupe.size
                group_has_deletion = True
                if is_partial:
                    partial += 1
                else:
                    full_content += 1
                # Only meaningful for files that would actually go: a cross-volume dupe
                # cannot be replaced by a hardlink to its reference.
                if not ref_device_read:
                    ref_device = device_of(group.ref.path)
                    ref_device_read = True
                device = device_of(dupe.path)
                if ref_device is not None and device is not None and device != ref_device:
                    cross_volume += 1
                    entry["cross_volume"] = True
                # Asked only when a probe was supplied, which the GUI does only when the user
                # has chosen to replace duplicates with clones. default_clone_probe performs a
                # real clone and removes it -- measured at 180 us per candidate on APFS, so
                # about 1.8 s over 10,000 -- and that is not a cost to pay for a question
                # nobody asked.
                #
                # Do not be tempted to memoise the probe itself on (device, directory). It
                # looks safe and is not: default_clone_probe also compares digests, so a False
                # caused by one group's mismatch would be reused for a different group in the
                # same directory whose files really are clonable. Only the filesystem question
                # inside it is cacheable, and that belongs there rather than here.
                if clone_probe is not None and clone_probe(dupe, group.ref):
                    cloneable += 1
                    entry["cloneable"] = True
            else:
                blocked[status] = blocked.get(status, 0) + 1
                blocked_bytes += dupe.size

            group_entries.append(entry)

        if group_entries:
            if group_has_deletion:
                group_count += 1
            # Classified even when every candidate is blocked: the entry is still reported, and a
            # group whose confidence went unstated reads as though it had none.
            group_confidence = classify_group(group)
            confidence[group_confidence.tier] += 1
            entries.append(
                {
                    "reference": {"path": str(group.ref.path), "size": group.ref.size},
                    "confidence": group_confidence.tier,
                    "confidence_reason": group_confidence.reason,
                    "duplicates": group_entries,
                }
            )

    return DeletionPlan(
        groups=group_count,
        files=files,
        total_bytes=total_bytes,
        partial=partial,
        full_content=full_content,
        blocked=blocked,
        blocked_bytes=blocked_bytes,
        cross_volume=cross_volume,
        cloneable=cloneable,
        confidence=confidence,
        entries=entries,
    )


def summarize_plan(plan: DeletionPlan, direct_delete: bool = False, partial_hint: str = "") -> list:
    """The plan as human-readable lines, without a leading label or trailing instructions.

    Shared so the CLI's `--plan` and the GUI's preview describe an identical plan identically.
    A user who reads one and then the other should not have to work out whether a difference in
    wording means a difference in what will happen.

    *partial_hint* is appended to the partial-match line by front ends that can say something
    specific about how to allow them.
    """
    verb = "permanently delete" if direct_delete else "send to trash"
    lines = [
        f"would {verb} {plan.files} file(s) in {plan.groups} group(s), "
        f"reclaiming {format_size(plan.total_bytes, 2)}"
    ]
    if plan.partial or plan.full_content:
        lines.append(f"{plan.full_content} matched on full content")
    if plan.partial:
        lines.append(f"{plan.partial} matched on a partial (sampled) hash only{partial_hint}")
    # Group-level, unlike the file counts above: how much is known about each group as a whole.
    # Only tiers with groups in them are listed -- a run of zeroes says nothing worth a line.
    for tier in reversed(Confidence.ORDER):
        count = plan.confidence.get(tier, 0)
        if count:
            lines.append(f"{count} group(s) {Confidence.LABELS[tier].lower()}: {Confidence.EXPLANATIONS[tier]}")
    for status, count in sorted(plan.blocked.items()):
        lines.append(f"{count} would be skipped: {DELETE_STATUS_REASON[status]}")
    if plan.blocked_bytes:
        lines.append(f"{format_size(plan.blocked_bytes, 2)} would not be reclaimed because of those skips")
    if plan.cross_volume:
        lines.append(
            f"{plan.cross_volume} are on a different volume from their reference " "(hardlink replacement would fail)"
        )
    if plan.cloneable:
        lines.append(f"{plan.cloneable} could be replaced by a copy-on-write clone instead of being removed")
    return lines
