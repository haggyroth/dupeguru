"""dupeGuru command-line interface.

Usage:
    python -m dupeguru scan <folder> [<folder> ...] [options]
    python cli.py scan <folder> [<folder> ...] [options]

Exit codes:
    0  Scan completed, no duplicates found.
    1  Scan completed, duplicates found.
    2  Bad arguments or startup error.
    3  Scan failed (I/O error, corrupt cache, etc.).
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

EXIT_OK = 0
EXIT_DUPES_FOUND = 1
EXIT_BAD_ARGS = 2
EXIT_SCAN_ERROR = 3

# --- CLI name → ScanType ---------------------------------------------------
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
        # Non-interactive: auto-confirm (callers can check stderr messages).
        return True

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

def _run_scan(app: DupeGuru, verbose: bool) -> None:
    """Run the scan synchronously on the calling thread (no Qt event loop needed)."""
    scanner = app.SCANNER_CLASS()
    fs.filesdb.ignore_mtime = app.options.get("rehash_ignore_mtime", False)
    fs.filesdb.purge_if_stale()

    for k, v in app.options.items():
        if hasattr(scanner, k):
            setattr(scanner, k, v)

    if app.app_mode == AppMode.PICTURE:
        scanner.cache_path = app._get_picture_cache_path()

    def _progress(progress: int, desc: str = "") -> bool:
        if verbose and desc:
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

    if verbose:
        print(file=sys.stderr)  # end the progress line


# --- Result serialisation --------------------------------------------------

def _serialise_results(app: DupeGuru) -> dict:
    """Convert scan results to a plain dict suitable for JSON output."""
    groups_out = []
    total_dupe_count = 0
    total_dupe_size = 0

    for group in app.results.groups:
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
            total_dupe_count += 1
            total_dupe_size += dupe.size
        groups_out.append({"reference": ref_entry, "duplicates": dupes_out})

    return {
        "groups": groups_out,
        "stats": {
            "groups": len(groups_out),
            "total_duplicates": total_dupe_count,
            "total_duplicate_size_bytes": total_dupe_size,
            "discarded_files": app.discarded_file_count,
        },
    }


# --- Argument parser -------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupeguru scan",
        description=(
            "Scan one or more folders for duplicate files and report results as JSON.\n\n"
            "Exit codes: 0=no duplicates, 1=duplicates found, 2=bad arguments, "
            "3=scan error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folders",
        nargs="+",
        metavar="FOLDER",
        help="Folder(s) to scan for duplicates.",
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
        help="Scan and report without modifying or deleting anything (default behaviour; flag is a reminder).",
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
            "Scan algorithm. Defaults per mode: standard→contents, music→tag, "
            "picture→picture-contents.  "
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
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress and summary to stderr.",
    )
    return parser


# --- Main ------------------------------------------------------------------

def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve and validate folders ----------------------------------------
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
            f"Scanning {len(folders)} folder(s)  mode={args.mode}  "
            f"scan-type={scan_type_name}",
            file=sys.stderr,
        )

    # Run scan ------------------------------------------------------------
    try:
        _run_scan(app, args.verbose)
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
            + (f" ({discarded} file(s) discarded)" if discarded else "") + ".",
            file=sys.stderr,
        )

    # Serialise results ---------------------------------------------------
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
