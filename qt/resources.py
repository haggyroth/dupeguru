# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Access to the bundled images, without Qt's resource system.

Qt's `.qrc` resource system needs `pyrcc5` to compile a `.qrc` into a Python module, and
PyQt6 ships no equivalent -- Riverbank dropped the tool rather than porting it. So the
images are embedded by `resources_data.py`, generated from `dg.qrc` by
`build.py:build_qt_resources` and committed alongside it.

Embedding rather than reading `images/` off disk is deliberate. The old `.qrc` route put the
bytes inside a Python module, which is why frozen builds worked without `images/` being
bundled as data files -- `package.py` copies only two logos. Switching to disk loading would
have traded a build-time problem for a packaging one, and packaging is the thing this project
cannot verify (see issue #10). Embedding keeps the property that already worked.

Committing the generated module rather than producing it during the build is also deliberate.
The previous build step shelled out to a bare `pyrcc5`, and when that was missing the shell
redirect still created an empty `dg_rc.py` while the build reported success -- a GUI with no
icons and nothing to explain it. There is now no build step to fail. `qt/tests/` regenerates
the data and compares, so the committed copy cannot drift from `images/` unnoticed.
"""

from base64 import b64decode
from functools import lru_cache

from qtpy.QtGui import QIcon, QPixmap

from qt.resources_data import RESOURCES


def names():
    """Every available resource name."""
    return sorted(RESOURCES)


def data(name: str) -> bytes:
    """Raw bytes for *name*. Raises KeyError if it does not exist."""
    return b64decode(RESOURCES[name])


@lru_cache(maxsize=None)
def pixmap(name: str) -> QPixmap:
    """QPixmap for *name*.

    Cached: these are read repeatedly while building menus and toolbars, and decoding the
    same PNG each time is pointless. Requires a QApplication, as QPixmap always has.
    """
    pix = QPixmap()
    pix.loadFromData(data(name))
    return pix


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """QIcon for *name*."""
    return QIcon(pixmap(name))
