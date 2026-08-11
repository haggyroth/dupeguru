# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Where the application keeps its data when there is no Qt to ask (issue #213).

``hscommon/desktop.py`` has two implementations: one that asks Qt, and a fallback for when no
binding can be imported. The fallback used to return the literal string ``"/tmp"``.

On macOS and Linux that is a real absolute directory, so it looked harmless for years. On
Windows ``/tmp`` is **drive-relative** -- it resolves against the current working directory's
drive -- so the packaged CLI, which ships without Qt on purpose, wrote its hash cache, its
ignore list and its **deletion log** to ``<cwd-drive>:\\tmp``, creating that directory at the
drive root and moving it depending on where the tool was launched from. A drive root is not
per-user: on a shared machine one person's deletion history was readable and writable by
another.

Two properties are checked here, and the platform is simulated rather than trusted, because
the failure is invisible on the platform these tests usually run on -- which is exactly how it
survived to a release:

- the path must carry a **drive**, on Windows. That is the bug, stated precisely: ``/tmp`` is
  not drive-relative anywhere else, and ``ntpath.isabs`` is the wrong check because it only
  started returning False for ``/tmp`` in Python 3.13 and this project supports 3.10.
- it must be the **same directory Qt resolves**, or the CLI and the GUI go on not sharing a
  hash cache, which is half the reason to fix this at all.

Qt is present wherever the suite runs, so the fallback is exercised in a subprocess with the
bindings hidden -- the same approach as ``core/tests/hash_algorithm_test.py``.
"""

import ntpath
import posixpath
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Hide every Qt binding, optionally pretend to be another platform, then report the paths.
_SCRIPT = """
import builtins, os, sys

real = builtins.__import__
def guard(name, *a, **k):
    if {hide_qt} and name.split(".")[0] in ("qtpy", "PyQt6", "PyQt5"):
        raise ImportError("hidden for this test")
    return real(name, *a, **k)
builtins.__import__ = guard

# Patched before hscommon.desktop is imported, because it binds these at import time.
import hscommon.plat as plat
if {platform!r} is not None:
    plat.ISWINDOWS = {platform!r} == "windows"
    plat.ISOSX = {platform!r} == "macos"

for key, value in {env!r}.items():
    os.environ[key] = value
for key in {unset!r}:
    os.environ.pop(key, None)

from hscommon import desktop
print(desktop.special_folder_path(desktop.SpecialFolder.APPDATA, portable={portable}))
print(desktop.special_folder_path(desktop.SpecialFolder.CACHE, portable={portable}))
"""


def resolve(hide_qt=True, platform=None, env=None, unset=(), portable=False):
    """Return (appdata, cache) as the target configuration would resolve them."""
    script = _SCRIPT.format(
        hide_qt=hide_qt,
        platform=platform,
        env=dict(env or {}),
        unset=tuple(unset),
        portable=portable,
    )
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()


WINDOWS_ENV = {"APPDATA": r"C:\Users\tester\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}


class TestTheWindowsFailure:
    """The bug itself: a path with no drive on the one platform where that matters."""

    def test_the_appdata_path_names_a_drive(self):
        appdata, _ = resolve(platform="windows", env=WINDOWS_ENV)
        drive, _ = ntpath.splitdrive(appdata)
        assert drive, f"{appdata!r} is drive-relative, so it moves with the working directory"

    def test_the_cache_path_names_a_drive(self):
        _, cache = resolve(platform="windows", env=WINDOWS_ENV)
        assert ntpath.splitdrive(cache)[0], f"{cache!r} is drive-relative"

    def test_it_is_under_the_users_own_appdata(self):
        # Per-user is the safety property. A drive root is writable by every authenticated user,
        # and the deletion log lives here.
        appdata, _ = resolve(platform="windows", env=WINDOWS_ENV)
        assert appdata.startswith(WINDOWS_ENV["APPDATA"]), appdata

    def test_it_is_not_a_drive_root(self):
        appdata, _ = resolve(platform="windows", env=WINDOWS_ENV)
        assert appdata.lower() not in (r"c:\tmp", "/tmp"), "still resolving to a drive root"
        assert ntpath.dirname(appdata) != ntpath.splitdrive(appdata)[0] + "\\"

    def test_the_cache_goes_under_local_appdata(self):
        # Qt puts CacheLocation under LOCALAPPDATA, not APPDATA, and appends a cache component
        # there and nowhere else.
        _, cache = resolve(platform="windows", env=WINDOWS_ENV)
        assert cache.startswith(WINDOWS_ENV["LOCALAPPDATA"]), cache
        assert cache.rstrip("\\").endswith("cache"), cache

    def test_a_missing_appdata_variable_still_yields_a_drive(self):
        # APPDATA is always set on a normal Windows session, but a service account or a stripped
        # environment is not normal and must not fall back to something drive-relative.
        appdata, _ = resolve(platform="windows", env={}, unset=("APPDATA", "LOCALAPPDATA"))
        assert ntpath.splitdrive(appdata)[0] or appdata.startswith("~") is False
        assert "/tmp" not in appdata


class TestItMatchesQt:
    """The other half of the fix: the CLI and the GUI must land in the same directory.

    Run on the real platform rather than a simulated one, because this is a comparison against
    what Qt actually does here.
    """

    def test_the_appdata_directory_is_the_one_qt_resolves(self):
        with_qt, _ = resolve(hide_qt=False)
        without_qt, _ = resolve(hide_qt=True)
        assert with_qt == without_qt, "the CLI and the GUI would not share a hash cache"

    def test_the_cache_directory_is_the_one_qt_resolves(self):
        _, with_qt = resolve(hide_qt=False)
        _, without_qt = resolve(hide_qt=True)
        assert with_qt == without_qt

    def test_the_organisation_name_is_part_of_the_path(self):
        # The tidy-looking simplification that would silently undo this: dropping the
        # organisation puts the CLI one directory away from the GUI, and everything still works
        # except the sharing.
        from core import __appname__, __orgname__

        appdata, _ = resolve(hide_qt=True)
        assert __orgname__ in appdata, appdata
        assert __appname__ in appdata, appdata


class TestTheOtherPlatforms:
    def test_linux_honours_xdg_data_home(self):
        appdata, _ = resolve(platform="linux", env={"XDG_DATA_HOME": "/home/tester/.local/share"})
        assert appdata.startswith("/home/tester/.local/share"), appdata

    def test_linux_falls_back_to_the_default_share_directory(self):
        appdata, _ = resolve(platform="linux", env={}, unset=("XDG_DATA_HOME",))
        assert posixpath.isabs(appdata), appdata
        assert ".local/share" in appdata, appdata

    def test_linux_honours_xdg_cache_home(self):
        _, cache = resolve(platform="linux", env={"XDG_CACHE_HOME": "/home/tester/.cache"})
        assert cache.startswith("/home/tester/.cache"), cache

    def test_macos_uses_application_support(self):
        appdata, _ = resolve(platform="macos")
        assert "Library/Application Support" in appdata, appdata

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_no_platform_returns_the_old_literal(self, platform):
        """The regression guard, stated as the thing that was actually wrong."""
        env = WINDOWS_ENV if platform == "windows" else {}
        appdata, cache = resolve(platform=platform, env=env)
        assert appdata != "/tmp"
        assert cache != "/tmp"


class TestPortableMode:
    """Portable mode must keep behaving as qt/util.py does, or a portable install starts
    writing to the user's profile instead of to its own folder."""

    def test_portable_appdata_sits_beside_the_executable(self):
        appdata, _ = resolve(platform="windows", env=WINDOWS_ENV, portable=True)
        assert appdata.endswith("data"), appdata
        assert not appdata.startswith(WINDOWS_ENV["APPDATA"]), "portable mode wrote to the profile"

    def test_portable_cache_sits_beside_the_executable_on_windows(self):
        _, cache = resolve(platform="windows", env=WINDOWS_ENV, portable=True)
        assert cache.endswith("cache"), cache
        assert not cache.startswith(WINDOWS_ENV["LOCALAPPDATA"]), "portable mode wrote to the profile"

    def test_portable_is_not_the_default(self):
        portable, _ = resolve(platform="windows", env=WINDOWS_ENV, portable=True)
        normal, _ = resolve(platform="windows", env=WINDOWS_ENV, portable=False)
        assert portable != normal
