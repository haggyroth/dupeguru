# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The hashing-process count survives closing the application.

The dialog tests prove the widget and the preference agree, and the app tests prove the
preference reaches the scanner. Neither touches storage, so a setting that was never written to
``QSettings`` -- or never read back -- passes all of them and then resets itself on every
restart. Found by reverting those two lines and watching the suite stay green.
"""

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qt.preferences import Preferences  # noqa: E402

KEY = "Workers"


@pytest.fixture
def stored():
    """A Preferences on the sandboxed settings, with this test's key removed afterwards.

    The settings file is shared by the session, so a value left behind here would be read by
    every later ``load()``.
    """
    prefs = Preferences()
    prefs._settings.remove(KEY)
    prefs._settings.sync()
    yield prefs
    prefs._settings.remove(KEY)
    prefs._settings.sync()


def test_the_default_is_automatic(stored):
    assert Preferences().workers == 0


def test_a_saved_count_is_read_back_by_a_fresh_instance(stored):
    # load() first, as the application does: save() writes attributes only load() creates.
    stored.load()
    stored.workers = 7
    stored.save()
    reloaded = Preferences()
    reloaded.load()
    assert reloaded.workers == 7


def test_with_nothing_stored_loading_keeps_automatic(stored):
    fresh = Preferences()
    fresh.load()
    assert fresh.workers == 0


def test_reset_puts_it_back_to_automatic(stored):
    stored.workers = 7
    stored.reset()
    assert stored.workers == 0
