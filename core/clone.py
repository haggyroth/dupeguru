# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Copy-on-write file clones, where the filesystem supports them.

A clone gives two independent files that share storage until one is written to. Unlike a
hardlink both paths keep their own metadata and editing one does not change the other; unlike
a symlink neither depends on the other continuing to exist. Deleting either leaves the other
intact and whole.

That makes it a fourth answer to "what do we do about this duplicate", alongside trash, delete
and link -- and the only one where nothing is lost. The space is reclaimed immediately because
the two files share their blocks.

Support is filesystem-specific and cannot be assumed:

* macOS APFS -- ``clonefile(2)``, available since 10.12
* Linux Btrfs, XFS, and some others -- the ``FICLONE`` ioctl
* everything else, including HFS+, exFAT, FAT and NTFS -- unsupported

Cloning also cannot cross filesystems, since the whole point is sharing blocks within one.

**Nothing here silently falls back.** A caller that cannot clone must be told so, because the
plausible fallbacks are both wrong: copying would double the space this is meant to reclaim,
and deleting would destroy the file the user was promised would survive.
"""

import ctypes
import ctypes.util
import errno
import logging
import os
import sys

# fcntl is Unix-only, and this module is imported by core.app -- so importing it
# unconditionally made dupeGuru fail to start on Windows entirely. Only Linux uses it.
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

# linux/fs.h: #define FICLONE _IOW(0x94, 9, int)
_FICLONE = 0x40049409

_ISMACOS = sys.platform == "darwin"
_ISLINUX = sys.platform.startswith("linux")


class CloneNotSupportedError(OSError):
    """Cloning is unavailable for this pair of paths.

    Raised rather than returning False so that a caller cannot ignore it by accident. The
    distinction matters: "this filesystem cannot clone" is a reason to stop, not a reason to
    try something destructive instead.
    """


def _load_clonefile():
    """Bind macOS clonefile(2), or None where it is unavailable."""
    if not _ISMACOS:
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fn = libc.clonefile
    except (OSError, AttributeError):  # pragma: no cover - only on an unexpected macOS
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    return fn


_clonefile = _load_clonefile()


def cloning_is_possible() -> bool:
    """Whether this platform has any clone mechanism at all.

    Says nothing about the filesystem in front of you -- APFS and HFS+ both answer True here,
    and only one of them can actually clone. Use :func:`can_clone` for a real answer.
    """
    return bool(_clonefile) or (_ISLINUX and fcntl is not None)


def clone_file(source: os.PathLike, dest: os.PathLike) -> None:
    """Create *dest* as a copy-on-write clone of *source*.

    *dest* must not exist. Raises :class:`CloneNotSupportedError` when the filesystem cannot
    do it, and lets any other OSError through unchanged -- a permission problem or a full disk
    is not a support problem and must not be reported as one.
    """
    src, dst = os.fspath(source), os.fspath(dest)
    if os.path.exists(dst):
        raise FileExistsError(errno.EEXIST, "clone destination already exists", dst)

    if _clonefile is not None:
        if _clonefile(src.encode(), dst.encode(), 0) == 0:
            return
        err = ctypes.get_errno()
        # ENOTSUP: filesystem cannot clone. EXDEV: the two paths are on different ones.
        if err in (errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP):
            raise CloneNotSupportedError(err, os.strerror(err), src)
        raise OSError(err, os.strerror(err), src)

    if _ISLINUX and fcntl is not None:
        src_fd = os.open(src, os.O_RDONLY)
        try:
            dst_fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                try:
                    fcntl.ioctl(dst_fd, _FICLONE, src_fd)
                except OSError as e:
                    if e.errno in (errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP, errno.EINVAL):
                        raise CloneNotSupportedError(e.errno, os.strerror(e.errno), src) from e
                    raise
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        # FICLONE copies data only; carry the mode and timestamps over so the clone is a
        # faithful stand-in for the file it replaces.
        st = os.stat(src)
        os.chmod(dst, st.st_mode)
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        return

    raise CloneNotSupportedError(errno.ENOTSUP, "cloning is not supported on this platform", src)


def can_clone(source: os.PathLike, dest_dir: os.PathLike) -> bool:
    """Whether *source* could be cloned into *dest_dir*, tested rather than assumed.

    Filesystem support cannot be inferred from the platform: macOS mounts HFS+, exFAT and
    network volumes that cannot clone, and Linux mounts ext4 alongside Btrfs. Comparing
    ``st_dev`` would catch the cross-filesystem case but not the unsupported-filesystem one.

    So this performs a real clone to a temporary name and removes it. That costs two syscalls
    and no data copy -- a clone is metadata-only -- which is cheap enough to be worth the
    certainty before offering the user an option that might not work.
    """
    if not cloning_is_possible():
        return False
    probe = os.path.join(os.fspath(dest_dir), f".dupeguru-clone-probe-{os.getpid()}")
    try:
        clone_file(source, probe)
    except (CloneNotSupportedError, OSError):
        return False
    else:
        return True
    finally:
        try:
            os.unlink(probe)
        except OSError:
            logging.warning("Could not remove clone probe %r", probe)
