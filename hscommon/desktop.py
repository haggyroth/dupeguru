# Created By: Virgil Dupras
# Created On: 2013-10-12
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

from enum import Enum
from os import PathLike
import os.path as op
import logging


class SpecialFolder(Enum):
    APPDATA = 1
    CACHE = 2


def open_url(url: str) -> None:
    """Open ``url`` with the default browser."""
    _open_url(url)


def open_path(path: PathLike) -> None:
    """Open ``path`` with its associated application."""
    _open_path(str(path))


def reveal_path(path: PathLike) -> None:
    """Open the folder containing ``path`` with the default file browser."""
    _reveal_path(str(path))


def special_folder_path(special_folder: SpecialFolder, portable: bool = False) -> str:
    """Returns the path of ``special_folder``.

    ``special_folder`` is a SpecialFolder.* const. The result is the special folder for the current
    application. The running process' application info is used to determine relevant information.

    You can override the application name with ``appname``. This argument is ingored under Qt.
    """
    return _special_folder_path(special_folder, portable=portable)


try:
    from qtpy.QtCore import QUrl, QStandardPaths
    from qtpy.QtGui import QDesktopServices
    from qt.util import get_appdata
    from core.util import executable_folder
    from hscommon.plat import ISWINDOWS, ISOSX
    import subprocess

    def _open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _open_path(path: str) -> None:
        url = QUrl.fromLocalFile(str(path))
        QDesktopServices.openUrl(url)

    def _reveal_path(path: str) -> None:
        if ISWINDOWS:
            subprocess.run(["explorer", "/select,", op.abspath(path)])
        elif ISOSX:
            subprocess.run(["open", "-R", op.abspath(path)])
        else:
            _open_path(op.dirname(str(path)))

    def _special_folder_path(special_folder: SpecialFolder, portable: bool = False) -> str:
        if special_folder == SpecialFolder.CACHE:
            if ISWINDOWS and portable:
                folder = op.join(executable_folder(), "cache")
            else:
                folder = QStandardPaths.standardLocations(QStandardPaths.CacheLocation)[0]
        else:
            folder = get_appdata(portable)
        return folder

except ImportError:
    # No Qt binding. Three ordinary situations, none of them alarming: the test suite, a
    # source checkout without a GUI binding, and the packaged command-line build, which
    # excludes Qt deliberately -- it is 117 MB of it, needed only to decode images.
    #
    # That third case is why the folder resolution below is real rather than a stub. The first
    # two can live with anything; the packaged CLI is a production path, and it writes the
    # deletion log.
    import os

    from core import __appname__, __orgname__
    from core.util import executable_folder
    from hscommon.plat import ISOSX, ISWINDOWS

    #
    # Logged at debug rather than warning because of that third case. Opening a file manager
    # is not something a command-line scan ever does, and a warning on every single
    # invocation is noise in exactly the tool most likely to be run in a loop.
    logging.debug("No Qt binding available; desktop integration falls back to no-ops")

    def _open_url(url: str) -> None:
        # Dummy for tests
        pass

    def _open_path(path: str) -> None:
        # Dummy for tests
        pass

    def _reveal_path(path: str) -> None:
        # Dummy for tests
        pass

    def _appdata_base() -> str:
        """The per-user application-data directory, resolved the way Qt resolves it.

        Deliberately reproduces ``QStandardPaths.AppDataLocation`` rather than picking somewhere
        reasonable, because matching it *is* the point: the GUI has Qt and the packaged CLI does
        not, and any difference means the two never see each other's hash cache, ignore list or
        exclude list. Measured against Qt on macOS, both resolve to
        ``~/Library/Application Support/Hardcoded Software/dupeGuru``.

        Note that the organisation name is part of the path on every platform. Leaving it out --
        an easy and tidy-looking simplification -- would put the CLI one directory away from the
        GUI and quietly preserve the split this exists to close.
        """
        if ISWINDOWS:
            base = os.environ.get("APPDATA") or op.expanduser(op.join("~", "AppData", "Roaming"))
        elif ISOSX:
            base = op.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or op.expanduser("~/.local/share")
        return op.join(base, __orgname__, __appname__)

    def _cache_base() -> str:
        """Likewise for ``QStandardPaths.CacheLocation``.

        Windows is the odd one: Qt appends a ``cache`` component there and nowhere else.
        """
        if ISWINDOWS:
            base = os.environ.get("LOCALAPPDATA") or op.expanduser(op.join("~", "AppData", "Local"))
            return op.join(base, __orgname__, __appname__, "cache")
        if ISOSX:
            return op.join(op.expanduser("~/Library/Caches"), __orgname__, __appname__)
        base = os.environ.get("XDG_CACHE_HOME") or op.expanduser("~/.cache")
        return op.join(base, __orgname__, __appname__)

    def _special_folder_path(special_folder: SpecialFolder, portable: bool = False) -> str:
        """Where this application keeps its data, without Qt to ask.

        This used to return the literal string ``"/tmp"``, which is a real absolute directory on
        macOS and Linux and therefore looked harmless for years. On Windows ``/tmp`` is
        *drive-relative*: it resolves against the current working directory's drive, so the
        packaged CLI put its hash cache, its ignore list and -- the part that matters -- its
        deletion log in ``<cwd-drive>:\\tmp``, moving with wherever it happened to be launched
        from, and created that directory at the drive root if it was missing.

        A drive root is not per-user. The deletion log is the record of what was removed and
        where it went, and on a shared machine one user's was readable and writable by another
        (issue #213).

        The ``portable`` branches mirror ``qt/util.py`` exactly, for the same reason the paths
        above do.
        """
        if special_folder == SpecialFolder.CACHE:
            if ISWINDOWS and portable:
                return op.join(executable_folder(), "cache")
            return _cache_base()
        if portable:
            return op.join(executable_folder(), "data")
        return _appdata_base()
