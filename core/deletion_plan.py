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
from hscommon.util import format_size


# Why a candidate was refused, in words. Lives here with the plan so the CLI's wording and
# the GUI's cannot describe the same status differently.
DELETE_STATUS_REASON = {
    DeleteStatus.GONE: "file no longer exists",
    DeleteStatus.SYMLINK: "path is a symlink",
    DeleteStatus.UNREADABLE: "could not read file metadata",
    DeleteStatus.CHANGED: "file changed since last scan",
}


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
