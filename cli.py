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
    --allow-partial-matches
                     Permit deleting files matched only on a partial (sampled) hash.
                     Without it, --delete refuses when any such match is marked.
    --from-results F Re-use a prior JSON/NDJSON output instead of rescanning. Validates each
                     file's size and mtime before deleting; skips any that changed since the
                     prior scan. Combine with --delete --yes to act on saved results.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from core import fs, se
from core.app import AppMode, DupeGuru
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
            }
        )
    return {"reference": ref_entry, "duplicates": dupes_out}


def _serialise_results(app: DupeGuru) -> dict:
    """Convert scan results to a plain dict suitable for JSON output."""
    groups_out = []
    total_dupe_count = 0
    total_dupe_size = 0

    for group in app.results.groups:
        g = _group_to_dict(group)
        groups_out.append(g)
        total_dupe_count += len(g["duplicates"])
        total_dupe_size += sum(d["size"] for d in g["duplicates"])

    return {
        "groups": groups_out,
        "stats": {
            "groups": len(groups_out),
            "total_duplicates": total_dupe_count,
            "total_duplicate_size_bytes": total_dupe_size,
            "discarded_files": app.discarded_file_count,
        },
    }


def _emit_ndjson(app: DupeGuru, out) -> tuple[int, int, int]:
    """Write one JSON line per group then a stats line; return (groups, dupes, dupe_bytes)."""
    total_dupe_count = 0
    total_dupe_size = 0
    group_count = 0

    for group in app.results.groups:
        g = _group_to_dict(group)
        dupe_size = sum(d["size"] for d in g["duplicates"])
        total_dupe_count += len(g["duplicates"])
        total_dupe_size += dupe_size
        group_count += 1
        print(json.dumps({"type": "group", **g}, ensure_ascii=False), file=out)

    stats = {
        "type": "stats",
        "groups": group_count,
        "total_duplicates": total_dupe_count,
        "total_duplicate_size_bytes": total_dupe_size,
        "discarded_files": app.discarded_file_count,
    }
    print(json.dumps(stats, ensure_ascii=False), file=out)
    return group_count, total_dupe_count, total_dupe_size


# --- Deletion helpers -------------------------------------------------------


def _deletion_plan(app: DupeGuru) -> tuple[int, int, int]:
    """Return (file_count, total_bytes, partial_match_count) for what --delete would remove.

    Marks and then unmarks, leaving the results in their original state, so this is safe
    to call before either a dry run or a real deletion.
    """
    app.results.mark_all()
    count = 0
    total_bytes = 0
    partial = 0
    for dupe in app.results.dupes:
        if not app.results.is_marked(dupe):
            continue
        count += 1
        total_bytes += dupe.size
        group = app.results.get_group_of_duplicate(dupe)
        if group is not None:
            match = group.get_match_of(dupe)
            if match is not None and getattr(match, "partial", False):
                partial += 1
    app.results.mark_none()
    return count, total_bytes, partial


def _report_deletion_plan(count: int, total_bytes: int, partial: int, direct_delete: bool) -> None:
    """Print what a deletion would do, for --dry-run. Writes to stderr only."""
    verb = "permanently delete" if direct_delete else "send to trash"
    print("DRY RUN: no files have been deleted.", file=sys.stderr)
    print(f"  would {verb} {count} file(s), reclaiming {format_size(total_bytes, 2)}", file=sys.stderr)
    if partial:
        print(
            f"  {partial} of those matched on a partial (sampled) hash only and would be "
            "refused without --allow-partial-matches",
            file=sys.stderr,
        )
    print("  re-run without --dry-run to execute.", file=sys.stderr)


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


def _load_results_json(path: str) -> list[dict]:
    """Parse a prior JSON or NDJSON results file into a flat list of group dicts."""
    text = Path(path).read_text(encoding="utf-8")
    # Try regular JSON first (the default output format, even when pretty-printed).
    try:
        data = json.loads(text)
        return data.get("groups", [])
    except json.JSONDecodeError:
        pass
    # Fall back to NDJSON: one JSON object per line.
    groups = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)  # let parse errors propagate
        if obj.get("type") == "group":
            groups.append({"reference": obj["reference"], "duplicates": obj["duplicates"]})
    return groups


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
            if not p.exists():
                problems.append((dupe["path"], "file no longer exists"))
                continue
            if p.is_symlink():
                problems.append((dupe["path"], "skipped: path is a symlink"))
                continue
            try:
                st = p.stat()
            except OSError as e:
                problems.append((dupe["path"], str(e)))
                continue
            if st.st_size != dupe["size"] or abs(st.st_mtime - dupe["mtime"]) > 2:
                problems.append((dupe["path"], "skipped: file changed since last scan"))
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
        default=True,
        help="Exclude hardlinked file pairs from results (default: on).",
    )
    parser.add_argument(
        "--no-filter-hardlinks",
        dest="filter_hardlinks",
        action="store_false",
        help="Include hardlinked file pairs in results.",
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
        "--rehash-ignore-mtime",
        action="store_true",
        default=False,
        help="Always rehash files even if their modification time is unchanged.",
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
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error reading results file: {exc}", file=sys.stderr)
            return EXIT_BAD_ARGS

        group_count = len(groups)
        dupe_count = sum(len(g.get("duplicates", [])) for g in groups)

        if args.verbose:
            print(
                f"Loaded {group_count} group(s) with {dupe_count} duplicate(s) from {args.from_results}",
                file=sys.stderr,
            )

        if not wants_delete:
            # Just re-emit the loaded results without any scan.
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
        if args.dry_run:
            # --dry-run wins over --delete. Saved results carry no partial-match flag
            # (see issue #26), so the partial count is reported as unknown rather than
            # as zero, which would be a false reassurance.
            plan_bytes = sum(d["size"] for g in groups for d in g.get("duplicates", []) if not d.get("is_ref_folder"))
            plan_count = sum(1 for g in groups for d in g.get("duplicates", []) if not d.get("is_ref_folder"))
            _report_deletion_plan(plan_count, plan_bytes, 0, args.direct_delete)
            print(
                "  note: saved results do not record partial-hash matches, so none can be " "reported here",
                file=sys.stderr,
            )
            return EXIT_DUPES_FOUND if plan_count > 0 else EXIT_OK

        if not args.yes:
            print(
                f"error: --delete requires --yes to confirm deletion of {dupe_count} file(s). "
                "Re-run with --yes to proceed.",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS

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
    app.options["word_weighting"] = args.word_weighting
    app.options["match_similar_words"] = args.match_similar
    app.options["mix_file_kind"] = args.mix_file_kind
    app.options["size_threshold"] = args.min_size * 1024  # KB -> bytes
    app.options["large_size_threshold"] = args.max_size * 1024 * 1024  # MB -> bytes
    app.options["big_file_size_threshold"] = args.partial_hash_threshold * 1024 * 1024  # MiB -> bytes
    app.options["rehash_ignore_mtime"] = args.rehash_ignore_mtime

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

    # Deletion (scan path) ------------------------------------------------
    if wants_delete and args.dry_run:
        # --dry-run wins over --delete: report the plan, remove nothing, then fall
        # through to the normal results emission below.
        plan_count, plan_bytes, plan_partial = _deletion_plan(app)
        _report_deletion_plan(plan_count, plan_bytes, plan_partial, args.direct_delete)
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

        _, _, partial_count = _deletion_plan(app)
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
    sys.exit(main())
