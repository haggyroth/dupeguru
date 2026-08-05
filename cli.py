"""dupeGuru command-line interface.

Usage:
    dupeguru-scan <folder> [<folder> ...] [options]     # installed console script
    python cli.py <folder> [<folder> ...] [options]     # from a source checkout
    python -m dupeguru <folder> [<folder> ...] [options]

There is no "scan" subcommand: folders are positional arguments.

Exit codes:
    0  Scan completed, no duplicates found (or --from-results: nothing deleted).
    1  Scan completed, duplicates found (or --from-results: files deleted).
    2  Bad arguments or startup error.
    3  Scan failed / deletion errors encountered.

Output formats:
    Default  Pretty-printed JSON object with "groups" and "stats" keys.
    --ndjson One JSON object per line: group records followed by a stats record.
             Suitable for streaming large result sets through jq or similar tools.
             Each group line: {"type":"group","reference":{...},"duplicates":[...]}
             Final line:      {"type":"stats","groups":N,...}

Progress (stderr):
    --verbose        Human-readable progress messages.
    --progress-json  Machine-readable {"type":"progress","percent":N,"description":"..."} lines.
                     Combine with --ndjson for fully structured pipelines.

Deletion:
    --delete         Send all duplicate files (non-reference) to the system trash after scanning.
                     Requires --yes to confirm, or the flag is a no-op.
    --yes            Skip the interactive deletion confirmation prompt.
    --direct-delete  Permanently delete instead of sending to trash (use with care).
    --dry-run        Never delete. With --delete, reports what would be removed and exits
                     without removing it. Takes precedence over --delete.
    --plan           Report what a deletion would do and exit. Needs no --delete. Emits a
                     per-file JSON plan on stdout in place of the normal results, with a
                     would_delete verdict and match_confidence for every candidate.
    --allow-partial-matches
                     Permit deleting files matched only on a partial (sampled) hash.
                     Without it, --delete refuses when any such match is marked. This
                     applies to --from-results deletions too.
    --full-verify    Re-read partial-hash matches and compare full content, discarding
                     any that turn out not to match. Removes the need for
                     --allow-partial-matches by making the matches certain.
    --from-results F Re-use a prior JSON/NDJSON output instead of rescanning. Validates each
                     file's size and mtime before deleting; skips any that changed since the
                     prior scan. Combine with --delete --yes to act on saved results.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import NamedTuple

from core import fs, se
from core.app import AppMode, DeleteStatus, DupeGuru, check_deletable
from core.directories import AlreadyThereError, DirectoryState, InvalidPathError
from core.scanner import ScanType
from hscommon.jobprogress.job import Job
from hscommon.util import format_size

EXIT_OK = 0
EXIT_DUPES_FOUND = 1
EXIT_BAD_ARGS = 2
EXIT_SCAN_ERROR = 3

# --- CLI name -> ScanType ---------------------------------------------------
# Keys are the values accepted on the command line.
_SCAN_TYPE_MAP = {
    "filename": ScanType.FILENAME,
    "fields": ScanType.FIELDS,
    "fields-noorder": ScanType.FIELDSNOORDER,
    "tag": ScanType.TAG,
    "contents": ScanType.CONTENTS,
    "folders": ScanType.FOLDERS,
    "picture-contents": ScanType.FUZZYBLOCK,
    "exif-timestamp": ScanType.EXIFTIMESTAMP,
}

_DEFAULT_SCAN_TYPE = {
    AppMode.STANDARD: ScanType.CONTENTS,
    AppMode.MUSIC: ScanType.TAG,
    AppMode.PICTURE: ScanType.FUZZYBLOCK,
}

_MODE_MAP = {
    "standard": AppMode.STANDARD,
    "music": AppMode.MUSIC,
    "picture": AppMode.PICTURE,
}


# --- Headless view shim ----------------------------------------------------


class _HeadlessView:
    """Minimal view that satisfies DupeGuru's view interface without any GUI."""

    def get_default(self, key, fallback=None):
        return fallback

    def set_default(self, key, value):
        pass

    def show_message(self, msg):
        print(msg, file=sys.stderr)

    def open_url(self, url):
        pass

    def open_path(self, path):
        pass

    def reveal_path(self, path):
        pass

    def ask_yes_no(self, prompt):
        # Non-interactive, so fail closed: a confirmation nobody can answer is a "no".
        # Auto-confirming would silently accept any safety prompt core adds in future
        # (partial-hash warnings, cross-device scan warnings) without the user ever
        # seeing it. Deliberate confirmation is expressed by explicit flags such as
        # --yes and --allow-partial-matches instead.
        print(f"{prompt} [declined: non-interactive]", file=sys.stderr)
        return False

    def create_results_window(self):
        pass

    def show_results_window(self):
        pass

    def show_problem_dialog(self):
        pass

    def select_dest_folder(self, prompt):
        return None

    def select_dest_file(self, prompt, ext):
        return None


# --- Synchronous scan ------------------------------------------------------


def _wire_photo_class():
    """Point core.pe.photo at a concrete photo class.

    core/pe/photo.py leaves PLAT_SPECIFIC_PHOTO_CLASS as None for the UI layer to fill in,
    and qt/app.py does it when the GUI starts. The CLI never constructs the Qt application,
    so without this every picture-mode scan died on the first file with
    "AttributeError: 'NoneType' object has no attribute 'can_handle'".

    The Qt photo class decodes headlessly -- QImage needs no QApplication to read a file --
    so the CLI can use it directly rather than needing a second decoder.
    """
    import core.pe.photo

    if core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS is not None:
        return
    try:
        from qt.pe.photo import File as PlatSpecificPhoto
    except ImportError as e:
        raise SystemExit(
            "Picture mode needs a Qt binding for image decoding, and none could be "
            f"imported ({e}). Install one with: pip install -r requirements.txt"
        )
    core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = PlatSpecificPhoto


def _run_scan(app: DupeGuru, verbose: bool, progress_json: bool = False) -> None:
    """Run the scan synchronously on the calling thread (no Qt event loop needed)."""
    scanner = app.SCANNER_CLASS()
    fs.filesdb.ignore_mtime = app.options.get("rehash_ignore_mtime", False)
    fs.filesdb.purge_if_stale()
    from core.hash_cache import hashcachedb

    hashcachedb.purge_if_stale()

    for k, v in app.options.items():
        if hasattr(scanner, k):
            setattr(scanner, k, v)

    if app.app_mode == AppMode.PICTURE:
        scanner.cache_path = app._get_picture_cache_path()
        _wire_photo_class()

    def _progress(progress: int, desc: str = "") -> bool:
        if progress_json and desc:
            print(
                json.dumps({"type": "progress", "percent": progress, "description": desc}),
                file=sys.stderr,
                flush=True,
            )
        elif verbose and desc:
            print(f"\r  {desc}...{' ' * 10}", end="", file=sys.stderr, flush=True)
        return True  # returning False would cancel the job

    j = Job(1, _progress)

    if scanner.scan_type == ScanType.FOLDERS:
        files = list(app.directories.get_folders(folderclass=se.fs.Folder, j=j))
    else:
        files = list(app.directories.get_files(fileclasses=app.fileclasses, j=j))

    if app.options.get("ignore_hardlink_matches"):
        files = app._remove_hardlink_dupes(files)

    logging.debug("CLI scan: %d files collected", len(files))

    app.results.groups = scanner.get_dupe_groups(files, app.ignore_list, j)
    app.discarded_file_count = scanner.discarded_file_count
    app.discarded_partial_count = scanner.discarded_partial_count
    app.verified_partial_count = scanner.verified_partial_count

    fs.filesdb.commit()
    from core.hash_cache import hashcachedb

    hashcachedb.commit()

    if verbose and not progress_json:
        print(file=sys.stderr)  # end the \r progress line


# --- Result serialisation --------------------------------------------------


def _group_to_dict(group) -> dict:
    """Serialise a single duplicate group to a plain dict."""
    ref = group.ref
    ref_entry = {
        "path": str(ref.path),
        "size": ref.size,
        "mtime": ref.mtime,
        "is_ref_folder": bool(ref.is_ref),
    }
    dupes_out = []
    for dupe in group.dupes:
        match = group.get_match_of(dupe)
        dupes_out.append(
            {
                "path": str(dupe.path),
                "size": dupe.size,
                "mtime": dupe.mtime,
                "is_ref_folder": bool(dupe.is_ref),
                "match_percentage": match.percentage if match else 0,
                # A partial match was confirmed by sampled chunks, not a full content
                # comparison. match_percentage is 100 either way, so this flag is the
                # only thing distinguishing a probable duplicate from a certain one.
                "partial_match": bool(getattr(match, "partial", False)) if match else False,
            }
        )
    return {"reference": ref_entry, "duplicates": dupes_out}


def _serialise_results(app: DupeGuru) -> dict:
    """Convert scan results to a plain dict suitable for JSON output."""
    groups_out = []
    total_dupe_count = 0
    total_dupe_size = 0
    total_partial = 0

    for group in app.results.groups:
        g = _group_to_dict(group)
        groups_out.append(g)
        total_dupe_count += len(g["duplicates"])
        total_dupe_size += sum(d["size"] for d in g["duplicates"])
        total_partial += sum(1 for d in g["duplicates"] if d["partial_match"])

    return {
        "groups": groups_out,
        "stats": {
            "groups": len(groups_out),
            "total_duplicates": total_dupe_count,
            "total_duplicate_size_bytes": total_dupe_size,
            "partial_matches": total_partial,
            "discarded_files": app.discarded_file_count,
        },
    }


def _emit_ndjson(app: DupeGuru, out) -> tuple[int, int, int]:
    """Write one JSON line per group then a stats line; return (groups, dupes, dupe_bytes)."""
    total_dupe_count = 0
    total_dupe_size = 0
    total_partial = 0
    group_count = 0

    for group in app.results.groups:
        g = _group_to_dict(group)
        dupe_size = sum(d["size"] for d in g["duplicates"])
        total_dupe_count += len(g["duplicates"])
        total_dupe_size += dupe_size
        total_partial += sum(1 for d in g["duplicates"] if d["partial_match"])
        group_count += 1
        print(json.dumps({"type": "group", **g}, ensure_ascii=False), file=out)

    stats = {
        "type": "stats",
        "groups": group_count,
        "total_duplicates": total_dupe_count,
        "total_duplicate_size_bytes": total_dupe_size,
        "partial_matches": total_partial,
        "discarded_files": app.discarded_file_count,
    }
    print(json.dumps(stats, ensure_ascii=False), file=out)
    return group_count, total_dupe_count, total_dupe_size


# --- Deletion helpers -------------------------------------------------------


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
    entries: list  # per-group plan, for machine-readable output


def _device_of(path) -> int | None:
    """st_dev for *path*, or None if it cannot be read."""
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _plan_entry(path, size, mtime, is_partial: bool) -> tuple:
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
        entry["blocked_reason"] = _DELETE_STATUS_REASON[status]
    return status, would_delete, entry


def _deletion_plan(app: DupeGuru) -> DeletionPlan:
    """Compute what --delete would do, touching nothing.

    Marks and then unmarks, leaving the results in their original state, so this is safe
    to call before either a dry run or a real deletion.

    Every candidate is re-validated with check_deletable -- the same predicate the deletion
    itself uses -- so the plan reports the files that would be refused instead of assuming
    every marked file is removable. That costs a stat() per marked file, which is nothing
    beside the scan that produced the results.
    """
    app.results.mark_all()
    files = total_bytes = partial = full_content = blocked_bytes = cross_volume = 0
    blocked: dict = {}
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
            status, would_delete, entry = _plan_entry(dupe.path, dupe.size, dupe.mtime, is_partial)

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
                    ref_device = _device_of(group.ref.path)
                    ref_device_read = True
                device = _device_of(dupe.path)
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
            entries.append(
                {
                    "reference": {"path": str(group.ref.path), "size": group.ref.size},
                    "duplicates": group_entries,
                }
            )

    app.results.mark_none()
    return DeletionPlan(
        groups=group_count,
        files=files,
        total_bytes=total_bytes,
        partial=partial,
        full_content=full_content,
        blocked=blocked,
        blocked_bytes=blocked_bytes,
        cross_volume=cross_volume,
        entries=entries,
    )


def _plan_from_saved_results(groups: list[dict]) -> DeletionPlan:
    """The same plan, computed from a saved results file rather than a live scan.

    Files are re-validated exactly as the live planner does, so a plan built from a
    week-old results file correctly reports everything that has changed underneath it.
    """
    files = total_bytes = partial = full_content = blocked_bytes = cross_volume = 0
    blocked: dict = {}
    entries = []
    group_count = 0

    for group in groups:
        ref_path = group.get("reference", {}).get("path")
        ref_device = None
        ref_device_read = False
        group_entries = []
        group_has_deletion = False

        for dupe in group.get("duplicates", []):
            if dupe.get("is_ref_folder"):
                continue
            # Absent in results predating #26; _saved_partial_counts reports that gap.
            is_partial = bool(dupe.get("partial_match", False))
            status, would_delete, entry = _plan_entry(Path(dupe["path"]), dupe["size"], dupe["mtime"], is_partial)

            if would_delete:
                files += 1
                total_bytes += dupe["size"]
                group_has_deletion = True
                if is_partial:
                    partial += 1
                else:
                    full_content += 1
                if not ref_device_read:
                    ref_device = _device_of(Path(ref_path)) if ref_path else None
                    ref_device_read = True
                device = _device_of(Path(dupe["path"]))
                if ref_device is not None and device is not None and device != ref_device:
                    cross_volume += 1
                    entry["cross_volume"] = True
            else:
                blocked[status] = blocked.get(status, 0) + 1
                blocked_bytes += dupe["size"]

            group_entries.append(entry)

        if group_entries:
            if group_has_deletion:
                group_count += 1
            entries.append({"reference": {"path": ref_path}, "duplicates": group_entries})

    return DeletionPlan(
        groups=group_count,
        files=files,
        total_bytes=total_bytes,
        partial=partial,
        full_content=full_content,
        blocked=blocked,
        blocked_bytes=blocked_bytes,
        cross_volume=cross_volume,
        entries=entries,
    )


def _serialise_plan(plan: DeletionPlan) -> dict:
    """Machine-readable deletion plan for --plan, mirroring the results serialisation."""
    return {
        "plan": plan.entries,
        "stats": {
            "groups": plan.groups,
            "would_delete": plan.files,
            "reclaimed_bytes": plan.total_bytes,
            "full_content_matches": plan.full_content,
            "partial_matches": plan.partial,
            "blocked": {status: count for status, count in sorted(plan.blocked.items())},
            "blocked_bytes": plan.blocked_bytes,
            "cross_volume": plan.cross_volume,
        },
    }


# label and closing line, keyed by whether --plan (rather than --dry-run) asked for this.
_PLAN_LABELS = {
    False: ("DRY RUN", "  re-run without --dry-run to execute."),
    True: ("DELETION PLAN", "  nothing has been deleted. Re-run with --delete --yes to execute."),
}


def _emit_plan(payload: dict, output: str | None) -> None:
    """Write the machine-readable plan to --output or stdout."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text)


def _report_deletion_plan(
    plan: DeletionPlan,
    direct_delete: bool,
    label: str = "DRY RUN",
    footer: str = "  re-run without --dry-run to execute.",
) -> None:
    """Print what a deletion would do. Writes to stderr only."""
    verb = "permanently delete" if direct_delete else "send to trash"
    print(f"{label}: no files have been deleted.", file=sys.stderr)
    print(
        f"  would {verb} {plan.files} file(s) in {plan.groups} group(s), "
        f"reclaiming {format_size(plan.total_bytes, 2)}",
        file=sys.stderr,
    )
    if plan.partial or plan.full_content:
        print(f"  {plan.full_content} matched on full content", file=sys.stderr)
    if plan.partial:
        print(
            f"  {plan.partial} matched on a partial (sampled) hash only and would be "
            "refused without --allow-partial-matches",
            file=sys.stderr,
        )
    for status, count in sorted(plan.blocked.items()):
        print(f"  {count} would be skipped: {_DELETE_STATUS_REASON[status]}", file=sys.stderr)
    if plan.blocked_bytes:
        print(
            f"  {format_size(plan.blocked_bytes, 2)} would not be reclaimed because of those skips",
            file=sys.stderr,
        )
    if plan.cross_volume:
        print(
            f"  {plan.cross_volume} are on a different volume from their reference "
            "(hardlink replacement would fail)",
            file=sys.stderr,
        )
    print(footer, file=sys.stderr)


def _delete_dupes(app: DupeGuru, direct_delete: bool, verbose: bool) -> list[tuple]:
    """Mark all dupes in results then delete them. Returns list of (path, error) problems."""
    app.results.mark_all()

    problems = []

    def _op(dupe):
        app._do_delete_dupe(dupe, link_deleted=False, use_hardlinks=False, direct_deletion=direct_delete)

    app.results.perform_on_marked(_op, remove_from_results=True)
    problems = list(app.results.problems)

    if verbose:
        print(
            f"Deleted duplicates. {len(problems)} problem(s) encountered.",
            file=sys.stderr,
        )

    return problems


# --- Load saved results (--from-results) ------------------------------------

# Sentinel: None is a legitimate JSON document, so it cannot mark 'did not parse'.
_NOT_JSON = object()


def _load_results_json(path: str) -> list[dict]:
    """Parse a prior JSON or NDJSON results file into a flat list of group dicts.

    Raises ValueError for anything that is not a results file. Every failure here is a user
    pointing --from-results at the wrong path, so it has to arrive as a message rather than
    a traceback: this used to raise AttributeError on JSON that was not an object, KeyError
    on a group record missing its keys, and UnicodeDecodeError on a binary file.

    json.JSONDecodeError and UnicodeDecodeError are both ValueError subclasses, so a single
    except in the caller covers those and the checks below.
    """
    text = Path(path).read_text(encoding="utf-8")  # UnicodeDecodeError is a ValueError
    if not text.strip():
        # A scan with no duplicates still writes {"groups": [], "stats": {...}}, so an empty
        # file is never something this produced. Reporting "no duplicates" would be worse
        # than useless: it looks like a successful answer about the wrong file.
        raise ValueError("file is empty")
    # Try regular JSON first (the default output format, even when pretty-printed).
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _NOT_JSON
    if data is not _NOT_JSON:
        if not isinstance(data, dict):
            raise ValueError(f'expected a JSON object with a "groups" key, got {type(data).__name__}')
        groups = data.get("groups", [])
        if not isinstance(groups, list):
            raise ValueError(f'"groups" must be a list, got {type(groups).__name__}')
        return groups
    # Fall back to NDJSON: one JSON object per line.
    groups = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"line {lineno}: expected a JSON object, got {type(obj).__name__}")
        if obj.get("type") == "group":
            missing = [k for k in ("reference", "duplicates") if k not in obj]
            if missing:
                raise ValueError(f"line {lineno}: group record is missing {', '.join(missing)}")
            groups.append({"reference": obj["reference"], "duplicates": obj["duplicates"]})
    return groups


# Short, path-free reasons for CLI output. check_deletable's own messages embed the path,
# which the GUI problem dialog needs but which would be repeated here: problems are printed
# as "skipped <path>: <reason>".
# No "skipped:" prefix here: both consumers supply their own framing ("skipped <path>: ..."
# and "N would be skipped: ..."), and baking it in doubled the word in both.
_DELETE_STATUS_REASON = {
    DeleteStatus.GONE: "file no longer exists",
    DeleteStatus.SYMLINK: "path is a symlink",
    DeleteStatus.UNREADABLE: "could not read file metadata",
    DeleteStatus.CHANGED: "file changed since last scan",
}


def _saved_partial_counts(groups: list[dict]) -> tuple[int, bool]:
    """Return (partial_count, recorded) for the deletable entries in saved results.

    ``recorded`` is False when any entry predates the ``partial_match`` field. The count
    is then meaningless and must not be presented as a confirmed zero: a results file
    written before the field existed looks identical to one with no partial matches.
    """
    count = 0
    recorded = True
    for group in groups:
        for dupe in group.get("duplicates", []):
            if dupe.get("is_ref_folder"):
                continue
            if "partial_match" not in dupe:
                recorded = False
            elif dupe["partial_match"]:
                count += 1
    return count, recorded


def _delete_from_saved_results(
    groups: list[dict], direct_delete: bool, verbose: bool
) -> tuple[int, list[tuple[str, str]]]:
    """Delete dupe files listed in saved results after re-validating size/mtime.

    Returns (deleted_count, [(path, reason), ...]) where the second element lists
    files that were skipped due to validation failure or I/O error.
    """
    deleted = 0
    problems = []

    for group in groups:
        for dupe in group.get("duplicates", []):
            if dupe.get("is_ref_folder"):
                continue
            p = Path(dupe["path"])
            status, _ = check_deletable(p, dupe["size"], dupe["mtime"])
            if status != DeleteStatus.OK:
                # Unlike the live scan path, a vanished file is worth reporting here: these
                # results may be days old, and the user explicitly asked to delete it.
                problems.append((dupe["path"], _DELETE_STATUS_REASON[status]))
                continue
            try:
                if direct_delete:
                    if p.is_dir():
                        import shutil

                        shutil.rmtree(str(p))
                    else:
                        p.unlink()
                else:
                    from send2trash import send2trash

                    send2trash(str(p))
                deleted += 1
                if verbose:
                    print(f"  deleted: {p}", file=sys.stderr)
            except OSError as e:
                problems.append((dupe["path"], str(e)))

    return deleted, problems


# --- Argument parser -------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupeguru-scan",
        description=(
            "Scan one or more folders for duplicate files and report results as JSON.\n\n"
            "Exit codes: 0=no duplicates, 1=duplicates found, 2=bad arguments, "
            "3=scan error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folders",
        nargs="*",
        metavar="FOLDER",
        help="Folder(s) to scan for duplicates. Not required when --from-results is used.",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write JSON results to FILE instead of stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Never modify or delete anything. Scanning without --delete already does nothing, "
            "but when combined with --delete this reports what would be removed and then "
            "exits without removing it."
        ),
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help=(
            "Report what a deletion would do and exit without deleting. Implies no mutation "
            "and does not need --delete. Writes a per-file plan as JSON to stdout (or "
            "--output) instead of the normal results, and a summary to stderr. Each file is "
            "re-validated exactly as the deletion would, so files that changed since the "
            "scan are reported as skipped rather than counted as reclaimable."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=sorted(_MODE_MAP),
        default="standard",
        help="Scan mode: standard (default), music, or picture.",
    )
    parser.add_argument(
        "--scan-type",
        choices=sorted(_SCAN_TYPE_MAP),
        default=None,
        metavar="TYPE",
        help=(
            "Scan algorithm. Defaults per mode: standard->contents, music->tag, "
            "picture->picture-contents.  "
            f"Choices: {', '.join(sorted(_SCAN_TYPE_MAP))}."
        ),
    )
    parser.add_argument(
        "--ref",
        action="append",
        metavar="FOLDER",
        dest="ref_folders",
        help=(
            "Mark FOLDER as a Reference folder: its files are scanned but never "
            "considered for deletion. May be repeated."
        ),
    )
    parser.add_argument(
        "--filter-hardlinks",
        action="store_true",
        default=False,
        help=(
            "Exclude hardlinked file pairs from results. Off by default, matching the GUI: "
            "the same folders scanned either way return the same results."
        ),
    )
    parser.add_argument(
        "--no-filter-hardlinks",
        dest="filter_hardlinks",
        action="store_false",
        help="Include hardlinked file pairs in results (the default).",
    )

    # --- Exclusions ----------------------------------------------------------
    excl = parser.add_argument_group(
        "exclusions",
        "Keep files and folders out of the scan. Without any of these the scan walks "
        "everything under the given folders, including node_modules, .venv and the like.",
    )
    excl.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="REGEX",
        dest="exclude",
        help=(
            "Exclude files and folders matching REGEX. Matched against the file or folder "
            "name, or against the full path when the pattern contains a path separator. "
            "May be repeated. NOTE: adding any exclusion replaces the built-in "
            '"skip folders whose name starts with a dot" fallback, so pass '
            "--exclude-defaults alongside it to keep that behaviour."
        ),
    )
    excl.add_argument(
        "--exclude-from",
        metavar="FILE",
        help="Read exclusion regexes from FILE, one per line. Blank lines and # comments are ignored.",
    )
    excl.add_argument(
        "--exclude-defaults",
        action="store_true",
        help=(
            "Apply the same default exclusions as the GUI's Restore Defaults button: OS "
            "metadata (Thumbs.db, desktop.ini, .DS_Store), trash and recycle folders, and "
            "anything whose name starts with a dot."
        ),
    )
    excl.add_argument(
        "--ignore-list",
        metavar="FILE",
        help=(
            "Load an ignore_list.xml saved by the GUI. Pairs recorded there are never "
            "reported as matches with each other."
        ),
    )

    # --- Scanner knobs -------------------------------------------------------
    knobs = parser.add_argument_group(
        "scanner knobs",
        "Fine-tune the matching engine. Defaults match the GUI defaults.",
    )
    knobs.add_argument(
        "--min-match",
        type=int,
        default=80,
        metavar="PERCENT",
        help="Minimum match percentage to consider two files duplicates (default: 80).",
    )
    knobs.add_argument(
        "--match-scaled",
        action="store_true",
        default=False,
        help=(
            "Picture mode: also match images of different dimensions. Off by default, "
            "matching the GUI. Without it a resized copy of an image is never reported as "
            "a duplicate, at any --min-match value."
        ),
    )
    knobs.add_argument(
        "--word-weighting",
        action="store_true",
        default=False,
        help="Weight word matches by frequency when comparing filenames (filename/fields modes).",
    )
    knobs.add_argument(
        "--match-similar",
        action="store_true",
        default=False,
        help="Match similar (not just identical) words in filename/fields/tag modes.",
    )
    knobs.add_argument(
        "--mix-file-kind",
        action="store_true",
        default=False,
        help="Allow files with different extensions to match each other.",
    )
    knobs.add_argument(
        "--min-size",
        type=int,
        default=0,
        metavar="KB",
        help="Ignore files smaller than KB kilobytes (default: 0, no limit).",
    )
    knobs.add_argument(
        "--max-size",
        type=int,
        default=0,
        metavar="MB",
        help="Ignore files larger than MB megabytes (default: 0, no limit).",
    )
    knobs.add_argument(
        "--partial-hash-threshold",
        type=int,
        default=0,
        metavar="MiB",
        help=(
            "Use partial hashing for files larger than MiB mebibytes to speed up scanning "
            "(default: 0, disabled). May produce a small number of false positives."
        ),
    )
    knobs.add_argument(
        "--full-verify",
        action="store_true",
        help=(
            "Re-read and fully hash the files in partial-hash matches, discarding any that "
            "do not match on full content. Only affects scans using "
            "--partial-hash-threshold, and costs a second read of just those files."
        ),
    )
    knobs.add_argument(
        "--trust-cache-ignore-mtime",
        "--rehash-ignore-mtime",  # old spelling, kept so existing scripts keep working
        dest="trust_cache_ignore_mtime",
        action="store_true",
        default=False,
        help=(
            "Reuse a cached hash for any file whose size matches, even if its modification "
            "time changed. Faster on large rescans, but will miss an edit that left the size "
            "unchanged. The old name for this was --rehash-ignore-mtime, which described the "
            "opposite of what it does."
        ),
    )

    # --- Output format -------------------------------------------------------
    fmt = parser.add_argument_group("output format")
    fmt.add_argument(
        "--ndjson",
        action="store_true",
        help=(
            "Emit newline-delimited JSON instead of a single JSON object. "
            "Each group is one line; the final line is the stats record."
        ),
    )

    # --- Deletion ------------------------------------------------------------
    deletion = parser.add_argument_group(
        "deletion",
        "Delete duplicate files after scanning. Requires --yes to take effect.",
    )
    deletion.add_argument(
        "--delete",
        action="store_true",
        help="Send all non-reference duplicates to the system trash after scanning.",
    )
    deletion.add_argument(
        "--direct-delete",
        action="store_true",
        help="Permanently delete instead of sending to trash. Implies --delete.",
    )
    deletion.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion without an interactive prompt.",
    )
    deletion.add_argument(
        "--allow-partial-matches",
        action="store_true",
        help=(
            "Permit deleting files that were matched on a partial (sampled) hash rather than "
            "full content. Without this, --delete refuses when any such match is marked. "
            "Only relevant when --partial-hash-threshold is in use."
        ),
    )
    deletion.add_argument(
        "--from-results",
        metavar="FILE",
        help=(
            "Load a prior JSON or NDJSON results file instead of rescanning. "
            "Each file's size and mtime are re-validated before deletion. "
            "Combine with --delete --yes to act on saved results."
        ),
    )

    # --- Progress ------------------------------------------------------------
    prog = parser.add_argument_group("progress")
    prog.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print human-readable progress and summary to stderr.",
    )
    prog.add_argument(
        "--progress-json",
        action="store_true",
        help=(
            'Emit {"type":"progress","percent":N,"description":"..."} lines to stderr. '
            "Mutually exclusive with --verbose."
        ),
    )
    return parser


# --- Main ------------------------------------------------------------------


def _read_exclude_file(path: str) -> list[str]:
    """Read one exclusion regex per line. Blank lines and # comments are skipped."""
    patterns = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _apply_exclusions(app: DupeGuru, patterns: list[str], use_defaults: bool) -> list[tuple[str, str]]:
    """Add and mark exclusion regexes. Returns [(regex, reason), ...] for any rejected.

    A regex has to be *marked* as well as added: Directories consults
    ``exclude_list.mark_count`` and iterates only the marked patterns, so an added but
    unmarked regex silently does nothing.
    """
    from core.exclude import AlreadyThereException

    if use_defaults:
        app.exclude_list.restore_defaults()

    problems = []
    for regex in patterns:
        try:
            app.exclude_list.add(regex)
        except AlreadyThereException:
            pass  # already present, but it still needs marking below
        except Exception as exc:
            # ValueError for the forbidden over-broad patterns, re.error for bad syntax.
            problems.append((regex, str(exc) or type(exc).__name__))
            continue
        app.exclude_list.mark(regex)
    return problems


def _make_streams_utf8() -> None:
    """Stop a legacy console code page from killing output.

    On Windows, stdout/stderr default to the console code page (often cp1252). File paths
    routinely contain characters it cannot encode, and a single one raises
    UnicodeEncodeError mid-scan. errors="replace" keeps the run alive and substitutes the
    offending character rather than aborting.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # already replaced, e.g. by pytest's capture
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    _make_streams_utf8()
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --- Basic flag validation --------------------------------------------
    if args.verbose and args.progress_json:
        print("error: --verbose and --progress-json are mutually exclusive", file=sys.stderr)
        return EXIT_BAD_ARGS

    wants_delete = args.delete or args.direct_delete

    if args.from_results:
        # ----------------------------------------------------------------
        # --from-results path: load saved JSON/NDJSON and optionally delete
        # ----------------------------------------------------------------
        if args.folders:
            print("error: --from-results cannot be combined with folder arguments", file=sys.stderr)
            return EXIT_BAD_ARGS

        try:
            groups = _load_results_json(args.from_results)
        except (OSError, ValueError) as exc:  # JSONDecodeError/UnicodeDecodeError are ValueErrors
            print(f"error reading results file: {exc}", file=sys.stderr)
            return EXIT_BAD_ARGS

        group_count = len(groups)
        dupe_count = sum(len(g.get("duplicates", [])) for g in groups)

        if args.verbose:
            print(
                f"Loaded {group_count} group(s) with {dupe_count} duplicate(s) from {args.from_results}",
                file=sys.stderr,
            )

        if not wants_delete and not args.plan:
            # Just re-emit the loaded results without any scan. --plan needs no --delete,
            # so it has to bypass this and fall through to the planner below.
            if args.ndjson:
                for g in groups:
                    print(json.dumps({"type": "group", **g}, ensure_ascii=False))
                print(
                    json.dumps(
                        {
                            "type": "stats",
                            "groups": group_count,
                            "total_duplicates": dupe_count,
                            "total_duplicate_size_bytes": sum(
                                d["size"] for g in groups for d in g.get("duplicates", [])
                            ),
                            "discarded_files": 0,
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                total_size = sum(d["size"] for g in groups for d in g.get("duplicates", []))
                result = {
                    "groups": groups,
                    "stats": {
                        "groups": group_count,
                        "total_duplicates": dupe_count,
                        "total_duplicate_size_bytes": total_size,
                        "discarded_files": 0,
                    },
                }
                if args.output:
                    try:
                        Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                    except OSError as exc:
                        print(f"error writing output file: {exc}", file=sys.stderr)
                        return EXIT_SCAN_ERROR
                else:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
            return EXIT_DUPES_FOUND if group_count > 0 else EXIT_OK

        # Deletion from saved results
        plan_partial, partial_recorded = _saved_partial_counts(groups)

        if args.dry_run or args.plan:
            # --dry-run and --plan both win over --delete.
            plan = _plan_from_saved_results(groups)
            label, footer = _PLAN_LABELS[bool(args.plan)]
            _report_deletion_plan(plan, args.direct_delete, label=label, footer=footer)
            if not partial_recorded:
                print(
                    "  note: these saved results predate partial-match recording, so partial "
                    "matches cannot be reported here. Re-scan to get them.",
                    file=sys.stderr,
                )
            if args.plan:
                _emit_plan(_serialise_plan(plan), args.output)
            return EXIT_DUPES_FOUND if plan.files > 0 else EXIT_OK

        if not args.yes:
            print(
                f"error: --delete requires --yes to confirm deletion of {dupe_count} file(s). "
                "Re-run with --yes to proceed.",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS

        # Mirror the scan path's refusal to delete probable-only duplicates. Without this,
        # routing a deletion through --from-results silently bypassed the gate entirely.
        if plan_partial and not args.allow_partial_matches:
            print(
                f"error: {plan_partial} file(s) in these saved results were matched on a "
                "partial (sampled) hash, not a full content comparison. They are probable "
                "duplicates, but a false positive is possible.\n"
                "Re-run with --allow-partial-matches to delete them anyway.",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS
        if not partial_recorded:
            print(
                "warning: these saved results predate partial-match recording, so any "
                "partial matches in them cannot be detected or refused.",
                file=sys.stderr,
            )

        deleted, problems = _delete_from_saved_results(groups, args.direct_delete, args.verbose)

        if problems:
            for path, reason in problems:
                print(f"  skipped {path}: {reason}", file=sys.stderr)
            print(
                f"Deleted {deleted} file(s); {len(problems)} skipped. See above for details.",
                file=sys.stderr,
            )
            return EXIT_SCAN_ERROR

        if args.verbose:
            print(f"Deleted {deleted} file(s).", file=sys.stderr)

        return EXIT_DUPES_FOUND if deleted > 0 else EXIT_OK

    # --------------------------------------------------------------------
    # Normal scan path
    # --------------------------------------------------------------------

    # Resolve and validate folders ----------------------------------------
    if not args.folders:
        print("error: at least one FOLDER is required (or use --from-results)", file=sys.stderr)
        return EXIT_BAD_ARGS

    folders: list[Path] = []
    for raw in args.folders:
        p = Path(raw).resolve()
        if not p.exists():
            print(f"error: folder does not exist: {p}", file=sys.stderr)
            return EXIT_BAD_ARGS
        if not p.is_dir():
            print(f"error: not a directory: {p}", file=sys.stderr)
            return EXIT_BAD_ARGS
        folders.append(p)

    ref_folders: set[Path] = set()
    for raw in args.ref_folders or []:
        p = Path(raw).resolve()
        if not p.exists():
            print(f"error: reference folder does not exist: {p}", file=sys.stderr)
            return EXIT_BAD_ARGS
        ref_folders.add(p)

    # Mode & scan type ----------------------------------------------------
    mode = _MODE_MAP[args.mode]
    if args.scan_type:
        scan_type = _SCAN_TYPE_MAP[args.scan_type]
    else:
        scan_type = _DEFAULT_SCAN_TYPE[mode]

    # Build app -----------------------------------------------------------
    try:
        app = DupeGuru(view=_HeadlessView())
    except Exception as exc:
        print(f"error: failed to initialise dupeGuru: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS

    app.app_mode = mode
    app.options["scan_type"] = scan_type
    app.options["ignore_hardlink_matches"] = args.filter_hardlinks

    # Scanner knobs -------------------------------------------------------
    app.options["min_match_percentage"] = args.min_match
    app.options["match_scaled"] = args.match_scaled
    app.options["word_weighting"] = args.word_weighting
    app.options["match_similar_words"] = args.match_similar
    app.options["mix_file_kind"] = args.mix_file_kind
    app.options["size_threshold"] = args.min_size * 1024  # KB -> bytes
    app.options["large_size_threshold"] = args.max_size * 1024 * 1024  # MB -> bytes
    app.options["big_file_size_threshold"] = args.partial_hash_threshold * 1024 * 1024  # MiB -> bytes
    app.options["full_verify"] = args.full_verify
    # The core option keeps its original name; only the CLI spelling changed.
    app.options["rehash_ignore_mtime"] = args.trust_cache_ignore_mtime

    # Exclusions ----------------------------------------------------------
    # Applied before the directories are added, because Directories consults the
    # exclude list while walking rather than filtering afterwards.
    exclude_patterns = list(args.exclude)
    if args.exclude_from:
        try:
            exclude_patterns.extend(_read_exclude_file(args.exclude_from))
        except OSError as exc:
            print(f"error reading exclude file: {exc}", file=sys.stderr)
            app.close()
            return EXIT_BAD_ARGS

    rejected = _apply_exclusions(app, exclude_patterns, args.exclude_defaults)
    if rejected:
        for regex, reason in rejected:
            print(f"error: cannot use exclusion {regex!r}: {reason}", file=sys.stderr)
        app.close()
        return EXIT_BAD_ARGS

    if args.ignore_list:
        # IgnoreList.load_from_xml swallows every exception and returns silently, so a
        # missing or malformed file would leave the user believing their list applied.
        # Check it here instead of trusting the loader to complain.
        ignore_path = Path(args.ignore_list)
        if not ignore_path.is_file():
            print(f"error reading ignore list: no such file: {args.ignore_list}", file=sys.stderr)
            app.close()
            return EXIT_BAD_ARGS
        app.ignore_list.load_from_xml(str(ignore_path))
        if not len(app.ignore_list):
            print(
                f"warning: ignore list {args.ignore_list} loaded no entries; "
                "it may be empty or not an ignore_list.xml",
                file=sys.stderr,
            )

    if args.verbose and (exclude_patterns or args.exclude_defaults):
        print(
            f"Exclusions active: {app.exclude_list.mark_count} pattern(s)",
            file=sys.stderr,
        )

    # Add directories -----------------------------------------------------
    for folder in folders:
        try:
            app.directories.add_path(folder)
        except AlreadyThereError:
            pass
        except InvalidPathError:
            print(f"error: cannot add path: {folder}", file=sys.stderr)
            app.close()
            return EXIT_BAD_ARGS
        if folder in ref_folders:
            app.directories.set_state(folder, DirectoryState.REFERENCE)

    if args.verbose:
        _reverse_scan_type = {v: k for k, v in _SCAN_TYPE_MAP.items()}
        scan_type_name = args.scan_type or _reverse_scan_type.get(scan_type, str(scan_type))
        print(
            f"Scanning {len(folders)} folder(s)  mode={args.mode}  " f"scan-type={scan_type_name}",
            file=sys.stderr,
        )

    # Run scan ------------------------------------------------------------
    try:
        _run_scan(app, args.verbose, progress_json=args.progress_json)
    except Exception as exc:
        print(f"error during scan: {exc}", file=sys.stderr)
        logging.exception("CLI scan failed")
        app.close()
        return EXIT_SCAN_ERROR

    group_count = len(app.results.groups)

    if args.verbose:
        discarded = app.discarded_file_count
        print(
            f"Found {group_count} duplicate group(s)"
            + (f" ({discarded} file(s) discarded)" if discarded else "")
            + ".",
            file=sys.stderr,
        )

    # Report full verification unconditionally: a discarded pair means partial hashing
    # produced a false positive, which the user needs to know even without --verbose.
    if args.full_verify:
        verified = app.verified_partial_count
        rejected = app.discarded_partial_count
        if verified or rejected:
            print(
                f"Full verification: {verified} partial match(es) confirmed on full content, "
                f"{rejected} discarded as false positive(s).",
                file=sys.stderr,
            )
        elif args.verbose:
            print("Full verification: no partial matches to verify.", file=sys.stderr)

    # Deletion plan (scan path) --------------------------------------------
    if args.plan:
        # --plan implies no mutation, needs no --delete, and replaces the normal results
        # on stdout: "what would be removed" and "what matched" are different questions.
        plan = _deletion_plan(app)
        label, footer = _PLAN_LABELS[True]
        _report_deletion_plan(plan, args.direct_delete, label=label, footer=footer)
        try:
            _emit_plan(_serialise_plan(plan), args.output)
        except OSError as exc:
            print(f"error writing output file: {exc}", file=sys.stderr)
            app.close()
            return EXIT_SCAN_ERROR
        app.close()
        return EXIT_DUPES_FOUND if plan.files > 0 else EXIT_OK

    # Deletion (scan path) ------------------------------------------------
    if wants_delete and args.dry_run:
        # --dry-run wins over --delete: report the plan, remove nothing, then fall
        # through to the normal results emission below.
        _report_deletion_plan(_deletion_plan(app), args.direct_delete)
    elif wants_delete:
        if not args.yes:
            dupe_count = sum(len(g.dupes) for g in app.results.groups)
            print(
                f"error: --delete requires --yes to confirm deletion of {dupe_count} file(s). "
                "Re-run with --yes to proceed.",
                file=sys.stderr,
            )
            app.close()
            return EXIT_BAD_ARGS

        # Revalidating here means a partial match that would be skipped anyway (changed
        # since the scan) no longer blocks the whole deletion.
        partial_count = _deletion_plan(app).partial
        if partial_count and not args.allow_partial_matches:
            print(
                f"error: {partial_count} marked file(s) were matched on a partial (sampled) "
                "hash, not a full content comparison. They are probable duplicates, but a "
                "false positive is possible.\n"
                "Re-run with --allow-partial-matches to delete them anyway, or without "
                "--partial-hash-threshold to compare full contents.",
                file=sys.stderr,
            )
            app.close()
            return EXIT_BAD_ARGS

        problems = _delete_dupes(app, direct_delete=args.direct_delete, verbose=args.verbose)

        if problems:
            for dupe, reason in problems:
                print(f"  skipped {dupe.path}: {reason}", file=sys.stderr)
            print(
                f"{len(problems)} file(s) could not be deleted. See above for details.",
                file=sys.stderr,
            )
            app.close()
            return EXIT_SCAN_ERROR

        app.close()
        return EXIT_DUPES_FOUND if group_count > 0 else EXIT_OK

    # Emit results --------------------------------------------------------
    if args.ndjson:
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    group_count, _, _ = _emit_ndjson(app, f)
                if args.verbose:
                    print(f"Results written to {args.output}", file=sys.stderr)
            except OSError as exc:
                print(f"error writing output file: {exc}", file=sys.stderr)
                app.close()
                return EXIT_SCAN_ERROR
        else:
            group_count, _, _ = _emit_ndjson(app, sys.stdout)
    else:
        result = _serialise_results(app)
        json_output = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            try:
                Path(args.output).write_text(json_output, encoding="utf-8")
                if args.verbose:
                    print(f"Results written to {args.output}", file=sys.stderr)
            except OSError as exc:
                print(f"error writing output file: {exc}", file=sys.stderr)
                app.close()
                return EXIT_SCAN_ERROR
        else:
            print(json_output)

    app.close()
    return EXIT_DUPES_FOUND if group_count > 0 else EXIT_OK


if __name__ == "__main__":
    # See run.py: required before anything else in a frozen entry point.
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
