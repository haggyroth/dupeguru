# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Send a file to the trash and report where it landed (issue #125).

``send2trash()`` returns ``None``, which is why "offer to restore it" looks impossible at
first. But it is discarding the answer rather than lacking it -- every platform's underlying
API reports the destination:

* macOS: ``FSMoveObjectToTrashSync(&source, target, opts)`` takes an out ``FSRef`` naming
  where the file went. send2trash passes ``NULL``.
* Linux: the XDG spec writes ``<trash>/info/<name>.trashinfo`` alongside, carrying the
  original path and the deletion date.
* Windows: ``IFileOperationProgressSink.PostDeleteItem`` receives ``psiNewlyCreated``, and
  send2trash's own sink even stores it before dropping it on the floor.

The destination has to be *recorded*, not reconstructed. Measured on macOS 26: trashing a
second file of the same name produces ``"name.txt 01-43-28-622.txt"`` -- a millisecond
timestamp. Guessing that is hopeless, and guessing wrong is worse than not guessing, because
the predictable name is occupied by a *different* file that a restore would then put back.

Where the destination cannot be captured the file is still trashed correctly; only the offer
to restore it is withheld. That is the honest failure: a restore button that does nothing is
worse than no button, because people rely on it.
"""

import logging
import os
import os.path as op
import sys
from pathlib import Path
from urllib.parse import unquote

from send2trash import send2trash


def trash_file(path) -> str:
    """Send *path* to the trash. Returns where it landed, or ``""`` if that is unknown.

    Raises ``OSError`` if the file could not be trashed at all, matching ``send2trash``.
    """
    str_path = str(path)
    if sys.platform == "darwin":
        destination = _trash_macos(str_path)
        if destination:
            return destination
        # Fell through to the generic path below, which trashes without reporting.
    if sys.platform.startswith("linux"):
        send2trash(str_path)
        return _find_xdg_destination(str_path)
    if sys.platform == "win32":
        return _trash_windows(str_path)
    send2trash(str_path)
    return ""


def can_report_destination() -> bool:
    """Whether this platform can say where a trashed file went.

    Front ends use this to avoid promising a restore they cannot perform. This is a statement
    about the platform, not a guarantee for any one file: a particular deletion can still come
    back with no destination, and callers must treat an empty result as "no restore for this
    file" regardless of what this returns.
    """
    return sys.platform in ("darwin", "win32") or sys.platform.startswith("linux")


# --- macOS


def _trash_macos(str_path: str) -> str:
    """Trash via CoreServices, capturing the out-parameter send2trash discards."""
    try:
        from ctypes import POINTER, Structure, byref, c_char, c_char_p, c_uint32, cdll
        from ctypes.util import find_library

        foundation = cdll.LoadLibrary(find_library("Foundation"))
        core_services = cdll.LoadLibrary(find_library("CoreServices"))

        class FSRef(Structure):
            _fields_ = [("hidden", c_char * 80)]

        status_string = foundation.GetMacOSStatusCommentString
        status_string.restype = c_char_p
        make_ref = core_services.FSPathMakeRefWithOptions
        move_to_trash = core_services.FSMoveObjectToTrashSync
        ref_to_path = core_services.FSRefMakePath
        ref_to_path.argtypes = [POINTER(FSRef), c_char_p, c_uint32]

        def check(status):
            if status:
                raise OSError(status_string(status).decode("utf-8"))

        source = FSRef()
        # Do not follow a leaf symlink: trashing the target rather than the link would remove
        # a file the user never selected. Matches what send2trash does.
        check(make_ref(str_path.encode("utf-8"), 0x01, byref(source), None))
        target = FSRef()
        check(move_to_trash(byref(source), byref(target), 0))
        buf = bytes(1024)
        check(ref_to_path(byref(target), buf, len(buf)))
        return buf.split(b"\0", 1)[0].decode("utf-8")
    except OSError:
        # A genuine failure to trash. Let it propagate: the caller treats this as the file not
        # having been deleted, which is true.
        raise
    except Exception:
        # Anything else -- a missing symbol, a ctypes problem on some future macOS -- means we
        # could not use this path, not that the file is undeletable. Fall back to send2trash
        # and lose only the destination.
        logging.warning("Could not trash via CoreServices; falling back to send2trash", exc_info=True)
        return ""


# --- Windows


def _trash_windows(str_path: str) -> str:
    """Recycle via send2trash, capturing the destination from its own progress sink.

    ``IFileOperationProgressSink.PostDeleteItem`` receives ``psiNewlyCreated`` -- the item as
    it now exists in the Recycle Bin -- and send2trash's sink already stores it as ``newItem``.
    Nothing reads it, because ``create_sink()`` returns the *wrapped* COM object and the Python
    instance holding the attribute is discarded.

    So this swaps the factory rather than reimplementing the deletion. send2trash's
    ``PerformOperations`` call, its flags and its error handling are untouched, which matters:
    a mistake in a hand-rolled IFileOperation would break deleting on Windows, and a broken
    delete is far worse than a missing undo. The only new code runs before the deletion starts
    and after it finishes.
    """
    try:
        import pythoncom
        from win32com.shell import shell

        from send2trash.win import modern
        from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
    except Exception:
        # No pywin32, or a send2trash whose internals have moved. Recycle the normal way and
        # simply do not offer a restore for this file.
        logging.debug("Cannot capture the Recycle Bin location; recycling without it", exc_info=True)
        send2trash(str_path)
        return ""

    captured = {}
    original_create_sink = modern.create_sink

    def create_capturing_sink():
        # Keep the Python instance as well as the wrapper. Method calls dispatch through the
        # wrapper to this object, so PostDeleteItem sets newItem here.
        sink = FileOperationProgressSink()
        captured["sink"] = sink
        return pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)

    modern.create_sink = create_capturing_sink
    try:
        # Anything raised here is a genuine failure to recycle and belongs to the caller, which
        # treats it as the file not having been deleted. Deliberately not caught: swallowing it
        # and retrying would risk deleting twice.
        send2trash(str_path)
    finally:
        modern.create_sink = original_create_sink

    sink = captured.get("sink")
    destination = getattr(sink, "newItem", None) if sink is not None else None
    if not destination:
        # The file is recycled either way; only the undo is unavailable.
        logging.debug("The Recycle Bin location for %s was not reported", str_path)
        return ""
    return str(destination)


# --- Linux (XDG)


def _find_xdg_destination(str_path: str) -> str:
    """Locate the file send2trash just moved, using the .trashinfo record it wrote.

    The spec stores the original path in the info file, so this matches on that rather than
    guessing the trashed name -- which send2trash disambiguates with a counter on collision.
    """
    try:
        for trash_dir in _xdg_trash_dirs(str_path):
            info_dir = trash_dir / "info"
            files_dir = trash_dir / "files"
            if not info_dir.is_dir():
                continue
            newest = None
            for info in info_dir.glob("*.trashinfo"):
                original = _trashinfo_path(info)
                if original != str_path:
                    continue
                candidate = files_dir / info.name[: -len(".trashinfo")]
                if not candidate.exists():
                    continue
                # Several generations of the same path can sit in the trash at once; the one
                # just written is the newest.
                stamp = info.stat().st_mtime
                if newest is None or stamp > newest[0]:
                    newest = (stamp, str(candidate))
            if newest is not None:
                return newest[1]
    except OSError:
        logging.warning("Could not locate the trashed copy of %s", str_path, exc_info=True)
    return ""


def _xdg_trash_dirs(str_path: str):
    """Trash directories that could hold *str_path*: the home one, and the volume's own."""
    home = os.environ.get("XDG_DATA_HOME") or op.expanduser("~/.local/share")
    yield Path(home) / "Trash"
    # A file on another filesystem goes to a .Trash-<uid> at that filesystem's root.
    path = Path(str_path).resolve()
    for parent in [path, *path.parents]:
        if op.ismount(str(parent)):
            yield parent / f".Trash-{os.getuid()}"
            break


def _trashinfo_path(info: Path) -> str:
    """The original path recorded in a .trashinfo file, or ``""``."""
    try:
        for line in info.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Path="):
                return unquote(line[len("Path=") :].strip())
    except OSError:
        pass
    return ""
