# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Where the application data directory resolves to (issue #94).

The CLI used to write cached_pictures.db, hash_cache.db and hash_cache2.db straight into the
root of the user's application-data folder -- a 119 MB file sitting beside every other
application's directory -- because QStandardPaths only appends the organisation and
application name when they are set on the QCoreApplication, and only run.py set them.

The consequence that actually cost users time was not the mess: it was that the CLI and GUI
resolved to *different* directories, so neither could reuse the other's cached hashes.
"""

import pytest

qtpy = pytest.importorskip("qtpy", reason="these assert Qt's own path resolution")

from core import __appname__, __orgname__  # noqa: E402


@pytest.fixture
def clean_identity():
    """Run each test against an unset identity, then restore whatever was there.

    QCoreApplication names are process-global, and the other Qt tests construct an
    application that sets them. Without this, these tests would pass or fail depending on
    which file pytest happened to run first.
    """
    from qtpy.QtCore import QCoreApplication

    org = QCoreApplication.organizationName()
    app = QCoreApplication.applicationName()
    # Only the organisation is cleared, because that is the sentinel get_appdata tests.
    # applicationName cannot be meaningfully cleared -- Qt regenerates it from the
    # executable name -- which is precisely why it is the wrong thing to branch on.
    QCoreApplication.setOrganizationName("")
    yield
    QCoreApplication.setOrganizationName(org)
    QCoreApplication.setApplicationName(app)


def test_appdata_is_application_specific_without_a_front_end(clean_identity):
    """The regression: with no identity set, Qt returned the bare base directory."""
    from qt.util import get_appdata

    path = get_appdata()
    assert path.rstrip("/").endswith(__appname__), (
        f"application data would land in {path!r}, which is not application-specific -- "
        "the CLI would write its databases into the root of that directory"
    )


def test_appdata_includes_the_organisation(clean_identity):
    from qt.util import get_appdata

    assert __orgname__ in get_appdata()


def test_cli_and_gui_resolve_the_same_directory(clean_identity):
    """The point of the fix: one cache, shared by both front ends.

    The CLI path is whatever get_appdata returns with no identity set; the GUI path is what
    it returns after run.py has set the names. They must agree, or a scan in one front end
    does no work for the other.
    """
    from qtpy.QtCore import QCoreApplication
    from qt.util import get_appdata

    cli_path = get_appdata()

    QCoreApplication.setOrganizationName(__orgname__)
    QCoreApplication.setApplicationName(__appname__)
    gui_path = get_appdata()

    assert cli_path == gui_path


def test_an_explicit_identity_is_not_overwritten(clean_identity):
    """Only fill in what is missing; a caller that chose its own names keeps them."""
    from qtpy.QtCore import QCoreApplication
    from qt.util import get_appdata

    QCoreApplication.setOrganizationName("Someone Else")
    QCoreApplication.setApplicationName("OtherApp")
    path = get_appdata()
    assert "OtherApp" in path
    assert __appname__ not in path


def test_portable_mode_is_unaffected(clean_identity):
    """Portable installs deliberately keep their data beside the executable."""
    from qt.util import get_appdata

    assert get_appdata(portable=True).endswith("data")
