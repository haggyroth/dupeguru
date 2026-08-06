# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Fixtures for the Qt smoke tests.

Two things have to be true before a single widget is constructed:

* there must be no display requirement -- CI runners have no X server or window server, so
  the offscreen platform plugin is selected below, before Qt is imported anywhere;
* nothing may touch the developer's real settings or application data. ``qt.app.DupeGuru``
  builds a ``Preferences`` (which opens QSettings) and a ``core.app.DupeGuru`` (which
  creates its appdata directory and connects two SQLite caches inside it). Pointed at the
  real locations, running the suite would read and potentially rewrite the settings of an
  installed copy. ``QStandardPaths.setTestModeEnabled`` plus a redirected QSettings path
  moves both into a sandbox.

Qt is imported lazily inside the fixtures so that this file stays importable on Linux, where
requirements.txt installs no Qt binding at all. Imports go through qtpy, which selects the
installed binding; see qt/tests/app_test.py for how the absence of one is handled.
"""

import os

import pytest

# Must be in place before QApplication is created. Set it at import time rather than in a
# fixture: pytest imports conftest before anything else here, and any stray Qt import in a
# test module would otherwise get the native platform plugin first.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Deliberately not the real names. QStandardPaths derives the appdata directory from these,
# so a distinct pair keeps the sandbox clearly separate from an installed dupeGuru.
_TEST_ORG = "dupeguru-tests"
_TEST_APP = "dupeguru-tests"


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    """A single offscreen QApplication with isolated settings and appdata.

    Session-scoped because Qt permits only one QApplication per process.
    """
    from qtpy.QtCore import QCoreApplication, QSettings, QStandardPaths
    from qtpy.QtWidgets import QApplication

    settings_dir = tmp_path_factory.mktemp("qsettings")

    QCoreApplication.setOrganizationName(_TEST_ORG)
    QCoreApplication.setApplicationName(_TEST_APP)
    # Redirects every QStandardPaths location, including the AppDataLocation that
    # qt.util.get_appdata returns, into a per-user test area rather than the real one.
    QStandardPaths.setTestModeEnabled(True)
    # create_qsettings() falls through to a bare QSettings() off Windows, so it picks up
    # the default format and path set here.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))

    app = QApplication.instance() or QApplication([])
    yield app
    app.quit()


@pytest.fixture(scope="session")
def dgapp(qapp):
    """The real ``qt.app.DupeGuru``, built once for the session.

    Session-scoped on purpose: ``core.app.DupeGuru`` connects the module-level ``filesdb``
    and ``hashcachedb`` singletons, so building several of them in one process would have
    them fighting over the same connections.

    Tests that change preferences must put them back; see ``restore_prefs``.
    """
    from qt.app import DupeGuru

    app = DupeGuru()
    yield app
    # Disconnect the module-level caches this connected. Without it the two SQLite
    # connections survive to interpreter exit and are finalised by the garbage collector,
    # which is what produced "ResourceWarning: unclosed database" on every run (issue #84).
    # FilesDB batches writes and flushes on close, so an unclosed connection is also the
    # shape of a bug that loses cached hashes -- worth eliminating rather than silencing.
    app.model.close()


@pytest.fixture
def restore_prefs(dgapp):
    """Snapshot and restore the preference attributes a test touches.

    ``dgapp`` is session-scoped, so a test that flips a preference would otherwise leak it
    into every test that runs after it.
    """
    prefs = dgapp.prefs
    saved = {k: v for k, v in vars(prefs).items() if not k.startswith("_")}
    yield prefs
    for k, v in saved.items():
        setattr(prefs, k, v)


def pytest_report_header(config):
    """Name the Qt binding in the pytest header.

    qtpy prefers PyQt5 when several bindings are installed -- its order is
    ['pyqt5', 'pyside2', 'pyqt6', 'pyside6'] -- regardless of PyQt6 being this project's
    default. An environment that has both, which any checkout used to exercise the fallback
    will, therefore runs the whole Qt suite against the *fallback* while looking green.

    Printing it costs nothing and removes the guessing. Reading the QT_API environment
    variable does not work for this: qtpy sets it during import to whatever it resolved, so
    after `import qtpy` it always looks as if someone configured it deliberately.
    """
    try:
        import qtpy
    except ImportError:
        return "Qt binding: none installed (Qt tests will skip)"
    return f"Qt binding: {qtpy.API_NAME} (override with QT_API=pyqt6)"
