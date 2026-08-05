# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Locale names must be ones the platform actually accepts (issue #87).

Every run on macOS logged ``Couldn't set locale en`` and continued with the C locale, so
numbers were formatted without the user's thousands separator or decimal mark. The mapping
sent a bare language code to ``setlocale``, which macOS rejects outright; on Linux the
".UTF-8" suffix turned it into the equally invalid "en.UTF-8". glibc's alias table is what
made it look like it worked, which is why no Linux CI leg ever noticed.

Translations were never affected -- gettext looks its catalogues up by language independently
of setlocale -- which is why this survived: nothing visibly broke.
"""

import locale

import pytest

from hscommon.trans import get_locale_name, LANG2LOCALENAME


@pytest.fixture(autouse=True)
def restore_locale():
    """setlocale is process-global; a test must not leak a locale into the rest of the run."""
    saved = locale.setlocale(locale.LC_ALL)
    yield
    locale.setlocale(locale.LC_ALL, saved)


@pytest.mark.parametrize("lang", sorted(LANG2LOCALENAME))
def test_every_language_maps_to_a_territory_qualified_name(lang):
    """The structural invariant the bug violated.

    Checked for every language rather than just "en", so a future addition cannot
    reintroduce it. Deliberately structural: asserting that setlocale *succeeds* for all
    twenty would depend on which locales the machine has installed, and would fail on a
    minimal image for reasons that have nothing to do with this code.
    """
    name = LANG2LOCALENAME[lang]
    assert "_" in name, (
        f"{lang!r} maps to {name!r}, which has no territory. macOS rejects a bare language "
        "code, and on Linux the .UTF-8 suffix makes it invalid too."
    )


def test_english_locale_name_is_accepted_by_setlocale():
    """The specific failure, checked against the real platform.

    en_US is present on macOS, Windows and the Linux CI images, so this is expected to hold
    everywhere the suite runs. If it ever fails, that is worth knowing rather than skipping.
    """
    name = get_locale_name("en")
    locale.setlocale(locale.LC_ALL, name)  # raises locale.Error if the platform refuses


def test_unknown_language_still_returns_none():
    """The caller falls back to English on None; that path must keep working."""
    assert get_locale_name("zz") is None
