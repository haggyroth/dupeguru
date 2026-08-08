# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Fixtures for the core tests.

Constructing a ``core.app.DupeGuru`` connects two module-level SQLite caches inside whatever
``desktop.special_folder_path`` returns, and only ``DupeGuru.close()`` disconnects them. The
tests never called it, which had two consequences:

* the suite wrote ``hash_cache.db`` and ``hash_cache2.db`` into the developer's **real**
  application data directory. Confirmed by inspection: those files contained
  ``pytest-of-<user>/pytest-NNN/...`` paths;
* two connections were finalised by the garbage collector at interpreter exit, producing the
  ``ResourceWarning: unclosed database`` that every run ended with (issue #84).

Both are fixed by the same autouse fixture, which is why they are one change. The warning is
worth eliminating rather than silencing: ``FilesDB`` batches writes and flushes on ``close``,
so "a connection reached GC without being closed" is exactly the shape of a bug that loses
cached hashes. A standing warning about that condition is one you do not want to learn to
ignore.
"""

import pytest

from hscommon.testutil import app  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path_factory, monkeypatch):
    """Point application data at a per-test directory and close the caches afterwards.

    Autouse rather than opt-in: any test that builds a DupeGuru pollutes the real directory,
    and requiring each one to remember is how this went unnoticed in the first place.
    """
    from hscommon import desktop
    from core import app as core_app, fs
    from core.hash_cache import hashcachedb

    # Deliberately not under the test's own tmp_path: many tests scan tmp_path, and an
    # appdata directory inside it turns the cache databases into scan targets. That produced
    # 25 failures the first time this fixture was written.
    appdata = tmp_path_factory.mktemp("appdata")
    monkeypatch.setattr(desktop, "special_folder_path", lambda *a, **k: str(appdata))
    monkeypatch.setattr(core_app.desktop, "special_folder_path", lambda *a, **k: str(appdata))

    yield appdata

    # Close whatever the test connected. Both are module-level singletons, so a test that
    # built an app leaves them open for every later test and for the interpreter's exit.
    for db in (fs.filesdb, hashcachedb):
        try:
            db.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a test failure
            pass


@pytest.fixture(autouse=True)
def close_opened_databases():
    """Close every SQLite-backed cache a test opens.

    FilesDB and SqliteCache are constructed directly all over the tests and rarely closed, so
    each one reached the garbage collector still holding a connection -- which is what the
    ``ResourceWarning: unclosed database`` noise was. Tracking construction here rather than
    adding a close() call to twenty tests means a *future* test cannot reintroduce the leak
    by forgetting one, which is the failure mode that let this accumulate.
    """
    from core import fs

    classes = [fs.FilesDB]
    try:
        from core.pe.cache_sqlite import SqliteCache

        classes.append(SqliteCache)
    except ImportError:  # the compiled picture module is not always built
        pass
    try:
        from core.file_list_cache import FileListCache

        classes.append(FileListCache)
    except ImportError:
        pass

    opened = []
    originals = {}

    def track(cls):
        original = cls.__init__

        def tracking_init(self, *args, **kwargs):
            original(self, *args, **kwargs)
            opened.append(self)

        originals[cls] = original
        cls.__init__ = tracking_init

    for cls in classes:
        track(cls)
    try:
        yield
    finally:
        for cls, original in originals.items():
            cls.__init__ = original
        for db in opened:
            try:
                db.close()
            except Exception:  # noqa: BLE001 - teardown must not mask a test failure
                pass
