# Copyright 2017 Virgil Dupras
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import sys
import os
import os.path as op
import compileall
import shutil
import json
from argparse import ArgumentParser
import platform
import plistlib
import distro
import re

from hscommon.build import (
    BuildError,
    print_and_do,
    run_checked,
    copy_packages,
    build_debian_changelog,
    get_module_version,
    filereplace,
    copy,
    setup_package_argparser,
    copy_all,
)

ENTRY_SCRIPT = "run.py"
LOCALE_DIR = "build/locale"
HELP_DIR = "build/help"
DEFAULT_QT_API = "pyqt6"


def _nsis_fallback_paths():
    """Plausible makensis locations, for when it is not on PATH.

    Built from the ProgramFiles environment variables rather than hardcoded drive letters,
    since the install root is not always C:. Both layouts are covered: NSIS 3.x puts
    makensis.exe directly in its install directory, older builds used a Bin subdirectory.
    """
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    return [
        op.join(root, "NSIS", *parts)
        for root in roots
        if root
        for parts in (("makensis.exe",), ("Bin", "makensis.exe"))
    ]


def find_makensis():
    """Return a usable makensis path, or None.

    The path used to be hardcoded to one Program Files location, so an NSIS installed
    anywhere else -- a different drive, a per-user install, winget or Chocolatey -- produced
    a "not recognized" error whose exit code was then discarded, and the build reported
    success with no installer. Prefer PATH, then look in the usual places.
    """
    found = shutil.which("makensis")
    if found:
        return found
    return next((c for c in _nsis_fallback_paths() if op.isfile(c)), None)


def installer_path(bits, version_array):
    """Where setup.nsi's OutFile directive puts the installer."""
    return op.join("dist", "dupeGuru_win{0}_{1}.{2}.{3}.exe".format(bits, *version_array))


def pin_qt_binding():
    # qtpy picks whichever binding it finds first, and it prefers PyQt5 over PyQt6. On a build
    # machine with both installed that silently freezes the fallback binding instead of the
    # default one, which also leaves the uninstaller looking for a directory that is not there.
    # Set QT_API explicitly so the frozen build does not depend on what else is on the machine.
    # An override is honoured, so the PyQt5 fallback can still be packaged on purpose.
    api = os.environ.setdefault("QT_API", DEFAULT_QT_API)
    print("Freezing against Qt binding: {}".format(api))


def parse_args():
    parser = ArgumentParser()
    setup_package_argparser(parser)
    return parser.parse_args()


def check_loc_doc():
    if not op.exists(LOCALE_DIR):
        print('Locale files are missing. Have you run "build.py --loc"?')
    # include help files if they are built otherwise exit as they should be included?
    if not op.exists(HELP_DIR):
        print('Help files are missing. Have you run "build.py --doc"?')
    return op.exists(LOCALE_DIR) and op.exists(HELP_DIR)


def copy_files_to_package(destpath, packages, with_so):
    # when with_so is true, we keep .so files in the package, and otherwise, we don't. We need this
    # flag because when building debian src pkg, we *don't* want .so files (they're compiled later)
    # and when we're packaging under Arch, we're packaging a binary package, so we want them.
    if op.exists(destpath):
        shutil.rmtree(destpath)
    os.makedirs(destpath)
    shutil.copy(ENTRY_SCRIPT, op.join(destpath, ENTRY_SCRIPT))
    extra_ignores = ["*.so"] if not with_so else None
    copy_packages(packages, destpath, extra_ignores=extra_ignores)
    # include locale files if they are built otherwise exit as it will break
    # the localization
    if not check_loc_doc():
        print("Exiting...")
        return
    shutil.copytree(op.join("build", "help"), op.join(destpath, "help"))
    shutil.copytree(op.join("build", "locale"), op.join(destpath, "locale"))
    compileall.compile_dir(destpath)


def package_debian_distribution(distribution):
    app_version = get_module_version("core")
    version = "{}~{}".format(app_version, distribution)
    destpath = op.join("build", "dupeguru-{}".format(version))
    srcpath = op.join(destpath, "src")
    packages = ["hscommon", "core", "qt", "send2trash"]
    copy_files_to_package(srcpath, packages, with_so=False)
    os.mkdir(op.join(destpath, "modules"))
    copy_all(op.join("core", "pe", "modules", "*.*"), op.join(destpath, "modules"))
    copy(
        op.join("qt", "pe", "modules", "block.c"),
        op.join(destpath, "modules", "block_qt.c"),
    )
    copy(
        op.join("pkg", "debian", "build_pe_modules.py"),
        op.join(destpath, "build_pe_modules.py"),
    )
    debdest = op.join(destpath, "debian")
    debskel = op.join("pkg", "debian")
    os.makedirs(debdest)
    debopts = json.load(open(op.join(debskel, "dupeguru.json")))
    for fn in ["compat", "copyright", "dirs", "rules", "source"]:
        copy(op.join(debskel, fn), op.join(debdest, fn))
    filereplace(op.join(debskel, "control"), op.join(debdest, "control"), **debopts)
    filereplace(op.join(debskel, "Makefile"), op.join(destpath, "Makefile"), **debopts)
    filereplace(op.join(debskel, "dupeguru.desktop"), op.join(debdest, "dupeguru.desktop"), **debopts)
    changelogpath = op.join("help", "changelog")
    changelog_dest = op.join(debdest, "changelog")
    project_name = debopts["pkgname"]
    from_version = "2.9.2"
    build_debian_changelog(
        changelogpath,
        changelog_dest,
        project_name,
        from_version=from_version,
        distribution=distribution,
    )
    shutil.copy(op.join("images", "dgse_logo_128.png"), srcpath)
    os.chdir(destpath)
    try:
        run_checked("dpkg-buildpackage -F -us -uc")
    finally:
        os.chdir("../..")


def package_debian():
    print("Packaging for Debian/Ubuntu")
    for distribution in ["unstable"]:
        package_debian_distribution(distribution)


def package_arch():
    # For now, package_arch() will only copy the source files into build/. It copies less packages
    # than package_debian because there are more python packages available in Arch (so we don't
    # need to include them).
    print("Packaging for Arch")
    srcpath = op.join("build", "dupeguru-arch")
    packages = ["hscommon", "core", "qt"]
    copy_files_to_package(srcpath, packages, with_so=True)
    shutil.copy(op.join("images", "dgse_logo_128.png"), srcpath)
    debopts = json.load(open(op.join("pkg", "arch", "dupeguru.json")))
    filereplace(op.join("pkg", "arch", "dupeguru.desktop"), op.join(srcpath, "dupeguru.desktop"), **debopts)


def package_source_txz():
    print("Creating git archive")
    app_version = get_module_version("core")
    name = "dupeguru-src-{}.tar".format(app_version)
    base_path = os.getcwd()
    build_path = op.join(base_path, "build")
    dest = op.join(build_path, name)
    run_checked("git archive -o {} HEAD".format(dest), produces=dest)
    run_checked("xz {}".format(dest), produces=dest + ".xz")


def package_windows():
    app_version = get_module_version("core")
    arch = platform.architecture()[0]
    # Information to pass to pyinstaller and NSIS
    match = re.search("[0-9]+.[0-9]+.[0-9]+", app_version)
    version_array = match.group(0).split(".")
    match = re.search("[0-9]+", arch)
    bits = match.group(0)
    if bits == "64":
        arch = "x64"
    else:
        arch = "x86"
    # include locale files if they are built otherwise exit as it will break
    # the localization
    if not check_loc_doc():
        print("Exiting...")
        return 1
    # create version information file from template
    try:
        version_template = open("win_version_info.temp", "r")
        version_info = version_template.read()
        version_template.close()
        version_info_file = open("win_version_info.txt", "w")
        version_info_file.write(version_info.format(version_array[0], version_array[1], version_array[2], bits))
        version_info_file.close()
    except Exception:
        print("Error creating version info file, exiting...")
        return 1
    # run pyinstaller from here:
    pin_qt_binding()
    import PyInstaller.__main__

    # UCRT dlls are included if the system has the windows kit installed
    PyInstaller.__main__.run(
        [
            "--name=dupeguru-win{0}".format(bits),
            "--windowed",
            "--noconfirm",
            "--clean",
            "--icon=images/dgse_logo.ico",
            "--add-data={0};locale".format(LOCALE_DIR),
            "--add-data={0};help".format(HELP_DIR),
            "--version-file=win_version_info.txt",
            "--paths=C:\\Program Files (x86)\\Windows Kits\\10\\Redist\\ucrt\\DLLs\\{0}".format(arch),
            ENTRY_SCRIPT,
        ]
    )
    # remove version info file
    os.remove("win_version_info.txt")
    # Call NSIS. Every step below reports failure rather than returning None: PyInstaller has
    # already filled dist/ by this point, so a failed installer step leaves a tree that looks
    # like a successful build minus the one artifact anyone would ship.
    makensis = find_makensis()
    if makensis is None:
        print(
            "makensis not found. Install NSIS and put makensis on PATH, or install it under "
            "Program Files. Looked in: PATH, " + ", ".join(_nsis_fallback_paths())
        )
        return 1
    cmd = '"{0}" /DVERSIONMAJOR={1} /DVERSIONMINOR={2} /DVERSIONPATCH={3} /DBITS={4} setup.nsi'
    result = print_and_do(cmd.format(makensis, version_array[0], version_array[1], version_array[2], bits))
    if result != 0:
        print("makensis failed with exit code {0}; no installer was produced.".format(result))
        return result
    # Independent of the exit code: a tool can report success and still write nothing.
    installer = installer_path(bits, version_array)
    if not op.isfile(installer):
        print("makensis reported success but {0} does not exist.".format(installer))
        return 1
    print("Built {0}".format(installer))
    return 0


APP_BUNDLE = op.join("dist", "dupeguru.app")


def stamp_macos_bundle_version(app_path, version):
    """Write the application version into the bundle's Info.plist.

    PyInstaller has no command-line option for this, so without it the bundle ships
    CFBundleShortVersionString "0.0.0" and no CFBundleVersion at all: Finder and the About
    box report 0.0.0, and anything reading CFBundleVersion -- build_dmg names the disk image
    from it -- fails outright.
    """
    plist_path = op.join(app_path, "Contents", "Info.plist")
    with open(plist_path, "rb") as fp:
        plist = plistlib.load(fp)
    plist["CFBundleShortVersionString"] = version
    plist["CFBundleVersion"] = version
    with open(plist_path, "wb") as fp:
        plistlib.dump(plist, fp)
    print(f"Stamped {plist_path} with version {version}")


def package_macos():
    # include locale files if they are built otherwise exit as it will break
    # the localization
    if not check_loc_doc():
        print("Exiting")
        return 1
    # run pyinstaller from here:
    pin_qt_binding()
    import PyInstaller.__main__

    PyInstaller.__main__.run(
        [
            "--name=dupeguru",
            "--windowed",
            "--noconfirm",
            "--clean",
            "--icon=images/dupeguru.icns",
            "--osx-bundle-identifier=com.hardcoded-software.dupeguru",
            "--add-data={0}:locale".format(LOCALE_DIR),
            "--add-data={0}:help".format(HELP_DIR),
            "{0}".format(ENTRY_SCRIPT),
        ]
    )
    if not op.exists(APP_BUNDLE):
        print(f"PyInstaller reported success but {APP_BUNDLE} does not exist.")
        return 1
    stamp_macos_bundle_version(APP_BUNDLE, get_module_version("core"))
    print(f"Built {APP_BUNDLE}")
    return 0


def main():
    """Return a process exit status: 0 on success, non-zero when packaging failed.

    This returned None regardless, so `python package.py` exited 0 even when no artifact had
    been produced. Failures now arrive two ways: the Windows and macOS paths return a status
    directly, and any step run through run_checked raises BuildError, which is caught here.

    Still not covered: package_arch does no subprocess work at all, and the copy/filereplace
    helpers report problems by printing rather than raising, so a packaging run can still be
    incomplete without failing. Narrower than it was, not airtight.
    """
    try:
        return _dispatch(parse_args())
    except BuildError as e:
        print("Packaging failed: {0}".format(e))
        return 1


def _dispatch(args):
    if args.src_pkg:
        print("Creating source package for dupeGuru")
        return package_source_txz() or 0
    print("Packaging dupeGuru with UI qt")
    if sys.platform == "win32":
        return package_windows() or 0
    elif sys.platform == "darwin":
        return package_macos() or 0
    else:
        if not args.arch_pkg:
            distname = distro.id()
        else:
            distname = "arch"
        if distname == "arch":
            return package_arch() or 0
        else:
            return package_debian() or 0


if __name__ == "__main__":
    sys.exit(main())
