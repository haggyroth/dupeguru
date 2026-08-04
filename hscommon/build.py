# Created By: Virgil Dupras
# Created On: 2009-03-03
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)

# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""This module is a collection of function to help in HS apps build process.
"""

from argparse import ArgumentParser
import os
import sys
import os.path as op
import shutil
import tempfile
import plistlib
from subprocess import Popen
import re
import importlib
from datetime import datetime
import glob
from typing import Any, AnyStr, Callable, Dict, List, Union

from hscommon.plat import ISWINDOWS


class BuildError(Exception):
    """A build or packaging step failed, or produced nothing."""


def print_and_do(cmd: str) -> int:
    """Prints ``cmd`` and executes it in the shell.

    Returns the exit code. Note that this **fails open**: ignoring the return value is
    silent, and callers have done exactly that repeatedly. Prefer :func:`run_checked` for
    anything whose failure should stop the build.
    """
    print(cmd)
    p = Popen(cmd, shell=True)
    return p.wait()


def run_checked(cmd: str, produces: Union[os.PathLike, None] = None, min_size: int = 1) -> None:
    """Run ``cmd``, raising :class:`BuildError` unless it succeeds and produces its artifact.

    The counterpart to :func:`print_and_do`, and the difference is the point. print_and_do
    returns a code that is silently ignorable, and build steps in this project have been
    ignoring it: a missing ``pyrcc5`` produced an empty ``dg_rc.py`` and a "build succeeded"
    (issue #50), an uninstaller matched nothing and reported success (#27), and a failed
    ``makensis`` exited 0 with no installer (#63). Each was invisible because the surrounding
    output looked normal.

    This raises instead, so ignoring a failure requires actively catching it rather than
    merely forgetting to look. ``produces`` additionally asserts the artifact exists and is
    not empty, because a tool can report success and still write nothing -- which is the half
    an exit-code check alone does not cover.
    """
    result = print_and_do(cmd)
    if result != 0:
        raise BuildError(f"command failed with exit code {result}: {cmd}")
    if produces is None:
        return
    if not op.exists(produces):
        raise BuildError(f"command reported success but did not create {produces}: {cmd}")
    if op.isfile(produces) and op.getsize(produces) < min_size:
        raise BuildError(f"command created {produces} but it is empty (< {min_size} bytes): {cmd}")


def _perform(src: os.PathLike, dst: os.PathLike, action: Callable, actionname: str) -> None:
    if not op.lexists(src):
        print("Copying %s failed: it doesn't exist." % src)
        return
    if op.lexists(dst):
        if op.isdir(dst):
            shutil.rmtree(dst)
        else:
            os.remove(dst)
    print("{} {} --> {}".format(actionname, src, dst))
    action(src, dst)


def copy_file_or_folder(src: os.PathLike, dst: os.PathLike) -> None:
    if op.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy(src, dst)


def move(src: os.PathLike, dst: os.PathLike) -> None:
    _perform(src, dst, os.rename, "Moving")


def copy(src: os.PathLike, dst: os.PathLike) -> None:
    _perform(src, dst, copy_file_or_folder, "Copying")


def _perform_on_all(pattern: AnyStr, dst: os.PathLike, action: Callable) -> None:
    # pattern is a glob pattern, example "folder/foo*". The file is moved directly in dst, no folder
    # structure from src is kept.
    filenames = glob.glob(pattern)
    for fn in filenames:
        destpath = op.join(dst, op.basename(fn))
        action(fn, destpath)


def move_all(pattern: AnyStr, dst: os.PathLike) -> None:
    _perform_on_all(pattern, dst, move)


def copy_all(pattern: AnyStr, dst: os.PathLike) -> None:
    _perform_on_all(pattern, dst, copy)


def filereplace(filename: os.PathLike, outfilename: Union[os.PathLike, None] = None, **kwargs) -> None:
    """Reads `filename`, replaces all {variables} in kwargs, and writes the result to `outfilename`."""
    if outfilename is None:
        outfilename = filename
    fp = open(filename, encoding="utf-8")
    contents = fp.read()
    fp.close()
    # We can't use str.format() because in some files, there might be {} characters that mess with it.
    for key, item in kwargs.items():
        contents = contents.replace(f"{{{key}}}", item)
    fp = open(outfilename, "wt", encoding="utf-8")
    fp.write(contents)
    fp.close()


def get_module_version(modulename: str) -> str:
    mod = importlib.import_module(modulename)
    return mod.__version__


def setup_package_argparser(parser: ArgumentParser):
    parser.add_argument(
        "--sign",
        dest="sign_identity",
        help="Sign app under specified identity before packaging (OS X only)",
    )
    parser.add_argument(
        "--nosign",
        action="store_true",
        dest="nosign",
        help="Don't sign the packaged app (OS X only)",
    )
    parser.add_argument(
        "--src-pkg",
        action="store_true",
        dest="src_pkg",
        help="Build a tar.gz of the current source.",
    )
    parser.add_argument(
        "--arch-pkg",
        action="store_true",
        dest="arch_pkg",
        help="Force Arch Linux packaging type, regardless of distro name.",
    )


# `args` come from an ArgumentParser updated with setup_package_argparser()
def package_cocoa_app_in_dmg(app_path: os.PathLike, destfolder: os.PathLike, args) -> None:
    # Rather than signing our app in XCode during the build phase, we sign it during the package
    # phase because running the app before packaging can modify it and we want to be sure to have
    # a valid signature.
    if args.sign_identity:
        sign_identity = f"Developer ID Application: {args.sign_identity}"
        result = print_and_do(f'codesign --force --deep --sign "{sign_identity}" "{app_path}"')
        if result != 0:
            print("ERROR: Signing failed. Aborting packaging.")
            return
    elif not args.nosign:
        print("ERROR: Either --nosign or --sign argument required.")
        return
    build_dmg(app_path, destfolder)


def build_dmg(app_path: os.PathLike, destfolder: os.PathLike) -> None:
    """Builds a DMG volume with application at ``app_path`` and puts it in ``dest_path``.

    The name of the resulting DMG volume is determined by the app's name and version.
    """
    print(repr(op.join(app_path, "Contents", "Info.plist")))
    with open(op.join(app_path, "Contents", "Info.plist"), "rb") as fp:
        plist = plistlib.load(fp)
    workpath = tempfile.mkdtemp()
    dmgpath = op.join(workpath, plist["CFBundleName"])
    os.mkdir(dmgpath)
    run_checked('cp -R "{}" "{}"'.format(app_path, dmgpath))
    run_checked('ln -s /Applications "%s"' % op.join(dmgpath, "Applications"))
    dmgname = "{}_osx_{}.dmg".format(
        plist["CFBundleName"].lower().replace(" ", "_"),
        plist["CFBundleVersion"].replace(".", "_"),
    )
    dmgpath_out = op.join(destfolder, dmgname)
    print("Building %s" % dmgname)
    # UDBZ = bzip compression. UDZO (zip compression) was used before, but it compresses much less.
    # Checked, and the .dmg asserted to exist: this used to print "Build Complete" whether or
    # not hdiutil had produced anything.
    run_checked(
        'hdiutil create "{}" -format UDBZ -nocrossdev -srcdir "{}"'.format(dmgpath_out, dmgpath),
        produces=dmgpath_out,
    )
    print("Build Complete")


def add_to_pythonpath(path: os.PathLike) -> None:
    """Adds ``path`` to both ``PYTHONPATH`` env and ``sys.path``."""
    abspath = op.abspath(path)
    pythonpath = os.environ.get("PYTHONPATH", "")
    pathsep = ";" if ISWINDOWS else ":"
    pythonpath = pathsep.join([abspath, pythonpath]) if pythonpath else abspath
    os.environ["PYTHONPATH"] = pythonpath
    sys.path.insert(1, abspath)


# This is a method to hack around those freakingly tricky data inclusion/exlusion rules
# in setuptools. We copy the packages *without data* in a build folder and then build the plugin
# from there.
def copy_packages(
    packages_names: List[str],
    dest: os.PathLike,
    create_links: bool = False,
    extra_ignores: Union[List[str], None] = None,
) -> None:
    """Copy python packages ``packages_names`` to ``dest``, spurious data.

    Copy will happen without tests, testdata, mercurial data or C extension module source with it.
    ``py2app`` include and exclude rules are **quite** funky, and doing this is the only reliable
    way to make sure we don't end up with useless stuff in our app.
    """
    if ISWINDOWS:
        create_links = False
    if not extra_ignores:
        extra_ignores = []
    ignore = shutil.ignore_patterns(".hg*", "tests", "testdata", "modules", "docs", "locale", *extra_ignores)
    for package_name in packages_names:
        if op.exists(package_name):
            source_path = package_name
        else:
            mod = __import__(package_name)
            source_path = mod.__file__
            if mod.__file__.endswith("__init__.py"):
                source_path = op.dirname(source_path)
        dest_name = op.basename(source_path)
        dest_path = op.join(dest, dest_name)
        if op.exists(dest_path):
            if op.islink(dest_path):
                os.unlink(dest_path)
            else:
                shutil.rmtree(dest_path)
        print(f"Copying package at {source_path} to {dest_path}")
        if create_links:
            os.symlink(op.abspath(source_path), dest_path)
        else:
            if op.isdir(source_path):
                shutil.copytree(source_path, dest_path, ignore=ignore)
            else:
                shutil.copy(source_path, dest_path)


def build_debian_changelog(
    changelogpath: os.PathLike,
    destfile: os.PathLike,
    pkgname: str,
    from_version: Union[str, None] = None,
    distribution: str = "precise",
    fix_version: Union[str, None] = None,
) -> None:
    """Builds a debian changelog out of a YAML changelog.

    Use fix_version to patch the top changelog to that version (if, for example, there was a
    packaging error and you need to quickly fix it)
    """

    def desc2list(desc):
        # We take each item, enumerated with the '*' character, and transform it into a list.
        desc = desc.replace("\n", " ")
        desc = desc.replace("  ", " ")
        result = desc.split("*")
        return [s.strip() for s in result if s.strip()]

    ENTRY_MODEL = (
        "{pkg} ({version}) {distribution}; urgency=low\n\n{changes}\n "
        "-- Virgil Dupras <hsoft@hardcoded.net>  {date}\n\n"
    )
    CHANGE_MODEL = "  * {description}\n"
    changelogs = read_changelog_file(changelogpath)
    if from_version:
        # We only want logs from a particular version
        for index, log in enumerate(changelogs):
            if log["version"] == from_version:
                changelogs = changelogs[: index + 1]
                break
    if fix_version:
        changelogs[0]["version"] = fix_version
    rendered_logs = []
    for log in changelogs:
        version = log["version"]
        logdate = log["date"]
        desc = log["description"]
        rendered_date = logdate.strftime("%a, %d %b %Y 00:00:00 +0000")
        rendered_descs = [CHANGE_MODEL.format(description=d) for d in desc2list(desc)]
        changes = "".join(rendered_descs)
        rendered_log = ENTRY_MODEL.format(
            pkg=pkgname,
            version=version,
            changes=changes,
            date=rendered_date,
            distribution=distribution,
        )
        rendered_logs.append(rendered_log)
    result = "".join(rendered_logs)
    fp = open(destfile, "w")
    fp.write(result)
    fp.close()


re_changelog_header = re.compile(r"=== ([\d.b]*) \(([\d\-]*)\)")


def read_changelog_file(filename: os.PathLike) -> List[Dict[str, Any]]:
    def iter_by_three(it):
        while True:
            try:
                version = next(it)
                date = next(it)
                description = next(it)
            except StopIteration:
                return
            yield version, date, description

    with open(filename, encoding="utf-8") as fp:
        contents = fp.read()
    splitted = re_changelog_header.split(contents)[1:]  # the first item is empty
    result = []
    for version, date_str, description in iter_by_three(iter(splitted)):
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        d = {
            "date": date,
            "date_str": date_str,
            "version": version,
            "description": description.strip(),
        }
        result.append(d)
    return result
