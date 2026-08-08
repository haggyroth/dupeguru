# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Locations where deleting duplicates tends to break installed software (issue #134).

Nothing stopped a user pointing dupeGuru at ``/System``, ``C:\\Windows`` or the inside of an
application bundle. Those trees legitimately contain many identical files -- framework
resources, bundled icons, duplicated libraries -- and removing or linking them breaks software
in ways that surface much later, a long way from the scan that caused them.

The existing guards do not cover this. ``check_deletable`` verifies a file has not *changed*;
it has no opinion on whether the file matters. The multiple-drives warning is about reference
folders being misconfigured, not about location.

**This warns; it never refuses.** Cleaning a duplicate-ridden application-support directory is
a real thing to want to do, and dupeGuru is not in a position to say the user is wrong about
their own machine.

**The list is deliberately small.** Any list of sensitive locations is incomplete, and an
ambitious one is worse than a short one: a prompt that fires on folders people scan routinely
gets dismissed reflexively, and that habit carries over to the two prompts that guard real
data-loss cases. So the bar for inclusion is "removing duplicates here can break the operating
system or an installed application", not "this folder looks technical". Ordinary user
directories -- Documents, Downloads, Pictures, external volumes -- must never match, and there
is a test asserting exactly that.
"""

import os
import tempfile
from pathlib import Path

from hscommon.plat import ISLINUX, ISOSX, ISWINDOWS

#: Reasons, kept as named constants so the same words reach every front end.
OS_ITSELF = "the operating system's own files are here"
INSTALLED_APPS = "installed applications live here"
APP_SUPPORT = "applications keep their working files here"
WHOLE_SYSTEM = "this is the entire filesystem"
INSIDE_A_BUNDLE = "this is inside an application bundle"


def _macos_locations() -> list:
    home = Path.home()
    return [
        (Path("/System"), OS_ITSELF),
        (Path("/Library"), OS_ITSELF),
        # /private itself is deliberately absent: it also holds the per-user temp directory,
        # so listing it would warn about scanning /tmp. /etc resolves into it, and is listed
        # by its real path for that reason.
        (Path("/private/etc"), OS_ITSELF),
        (Path("/usr"), OS_ITSELF),
        (Path("/bin"), OS_ITSELF),
        (Path("/sbin"), OS_ITSELF),
        (Path("/Applications"), INSTALLED_APPS),
        # Preferences, containers and keychains. Named in the issue as the case someone might
        # legitimately want to clean, which is exactly why this warns rather than refusing.
        (home / "Library", APP_SUPPORT),
    ]


def _windows_locations() -> list:
    def env(name):
        value = os.environ.get(name)
        return Path(value) if value else None

    candidates = [
        (env("SystemRoot") or Path("C:/Windows"), OS_ITSELF),
        (env("ProgramFiles") or Path("C:/Program Files"), INSTALLED_APPS),
        (env("ProgramFiles(x86)") or Path("C:/Program Files (x86)"), INSTALLED_APPS),
        (env("ProgramData"), APP_SUPPORT),
        (env("APPDATA"), APP_SUPPORT),
        (env("LOCALAPPDATA"), APP_SUPPORT),
    ]
    return [(path, reason) for path, reason in candidates if path is not None]


def _linux_locations() -> list:
    return [
        (Path("/usr"), OS_ITSELF),
        (Path("/bin"), OS_ITSELF),
        (Path("/sbin"), OS_ITSELF),
        (Path("/lib"), OS_ITSELF),
        (Path("/lib64"), OS_ITSELF),
        (Path("/etc"), OS_ITSELF),
        (Path("/boot"), OS_ITSELF),
        (Path("/var"), OS_ITSELF),
        (Path("/opt"), INSTALLED_APPS),
        (Path("/snap"), INSTALLED_APPS),
    ]


def known_locations() -> list:
    """(path, reason) pairs for this platform, most specific first.

    Built per call rather than at import: the home directory and the Windows environment are
    both things a test -- or a user switching accounts -- can change underneath us.
    """
    if ISOSX:
        locations = _macos_locations()
    elif ISWINDOWS:
        locations = _windows_locations()
    elif ISLINUX:
        locations = _linux_locations()
    else:
        locations = []
    # Longest path first, so a folder inside two listed locations gets the more specific reason.
    return sorted(locations, key=lambda pair: len(pair[0].parts), reverse=True)


def _normalise(path) -> Path:
    """*path* made comparable: absolute, with symlinks and ``..`` resolved.

    Resolved rather than merely absolute because ``/etc`` is a symlink to ``/private/etc`` on
    macOS, and a check that compared the unresolved path would miss every such alias.
    """
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(path).absolute()


def _is_within(path: Path, ancestor: Path) -> bool:
    """Whether *path* is *ancestor* or sits inside it, case-insensitively where that applies.

    macOS and Windows both have case-insensitive filesystems by default, so ``/system`` and
    ``/System`` are the same directory and a case-sensitive comparison would miss one of them.
    """
    if ISLINUX:
        first, second = path, ancestor
    else:
        first = Path(str(path).lower())
        second = Path(str(ancestor).lower())
    return first == second or second in first.parents


def reason_for(path) -> str:
    """Why deleting duplicates under *path* is risky, or ``""`` if it is not.

    A folder matches when it *is* a listed location or sits inside one. Selecting the root of
    the filesystem matches on its own account -- everything sensitive is inside it, and a scan
    of the whole machine is the case this is most worth saying something about.
    """
    resolved = _normalise(path)

    # Checked before the table: on Windows the root is a drive letter, so it is not a fixed
    # string, and on POSIX "/" is an ancestor of everything and would swallow every other rule.
    if resolved.parent == resolved:
        return WHOLE_SYSTEM

    # On Windows the temp directory lives inside %LOCALAPPDATA%, which is listed, so without
    # this every scan of a temp folder would warn -- and scanning one threatens nothing.
    #
    # This looks like dead code on macOS and Linux, where the temp directory is not inside any
    # listed location, and deleting it changes nothing you can observe there. It is load-bearing
    # on Windows only; the CI matrix is what proves it, not a local run.
    temp = _normalise(tempfile.gettempdir())
    if _is_within(resolved, temp):
        return ""

    if ISOSX and any(part.endswith(".app") for part in resolved.parts):
        return INSIDE_A_BUNDLE

    for location, reason in known_locations():
        if _is_within(resolved, location):
            return reason
    return ""


def warnings_for(paths) -> list:
    """(path, reason) for each of *paths* that is risky, in the order given.

    Returns pairs rather than a formatted string so each front end can present them its own
    way, and so the reason travels with the folder it belongs to -- a warning that lists three
    folders and one reason cannot say which folder the reason was about.
    """
    found = []
    for path in paths:
        reason = reason_for(path)
        if reason:
            found.append((Path(path), reason))
    return found


def describe(warnings) -> str:
    """The warnings as a message body, without a leading label or a trailing question.

    Shared so the GUI's prompt and the command line's warning cannot describe the same folders
    differently.
    """
    if not warnings:
        return ""
    lines = [f"{path} -- {reason}" for path, reason in warnings]
    subject = "This folder is" if len(lines) == 1 else "These folders are"
    return (
        f"{subject} a location where applications and the operating system keep their own "
        "files:\n\n"
        + "\n".join(lines)
        + "\n\nSuch places hold many identical files on purpose -- shared libraries, bundled "
        "resources, cached copies. Deleting or linking them can stop installed software from "
        "working, often long after the fact."
    )
