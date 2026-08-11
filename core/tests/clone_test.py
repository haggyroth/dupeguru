# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Copy-on-write clones as an alternative to deleting (issue #129).

The point of the feature is that nothing is lost: both paths remain independent files, and
the space is reclaimed because they share blocks. Most of these assert that independence,
because it is the entire difference between a clone and a hardlink, and the reason this is
safe to offer where linking is not.
"""

import errno
import logging
import os

import pytest

from core import clone

pytestmark = pytest.mark.skipif(not clone.cloning_is_possible(), reason="no clone mechanism on this platform")


@pytest.fixture
def cloneable(tmp_path):
    """A directory where cloning actually works, or a skip.

    Platform support is not filesystem support: macOS mounts HFS+ and exFAT, Linux mounts
    ext4. Probing is the only honest answer, and it is what can_clone does.
    """
    source = tmp_path / "source.bin"
    source.write_bytes(b"A" * 4096)
    if not clone.can_clone(source, tmp_path):
        pytest.skip("this filesystem cannot clone")
    return tmp_path, source


class TestCloneSemantics:
    def test_clone_has_the_same_contents(self, cloneable):
        tmp_path, source = cloneable
        dest = tmp_path / "clone.bin"
        clone.clone_file(source, dest)
        assert dest.read_bytes() == source.read_bytes()

    def test_clone_is_a_separate_file_not_a_hardlink(self, cloneable):
        """The distinction the whole feature rests on."""
        tmp_path, source = cloneable
        dest = tmp_path / "clone.bin"
        clone.clone_file(source, dest)
        assert os.stat(source).st_ino != os.stat(dest).st_ino

    def test_editing_the_clone_does_not_change_the_original(self, cloneable):
        """A hardlink would fail this. It is why cloning is safe where linking is not."""
        tmp_path, source = cloneable
        dest = tmp_path / "clone.bin"
        clone.clone_file(source, dest)
        with open(dest, "r+b") as fp:
            fp.write(b"Z" * 10)
        assert source.read_bytes()[:10] == b"A" * 10

    def test_deleting_the_original_leaves_the_clone_whole(self, cloneable):
        """A symlink would fail this."""
        tmp_path, source = cloneable
        dest = tmp_path / "clone.bin"
        clone.clone_file(source, dest)
        source.unlink()
        assert len(dest.read_bytes()) == 4096

    def test_clone_carries_the_modification_time(self, cloneable):
        """The clone stands in for a file the user had; it should not look newly created."""
        tmp_path, source = cloneable
        old = 1_000_000_000
        os.utime(source, (old, old))
        dest = tmp_path / "clone.bin"
        clone.clone_file(source, dest)
        assert int(os.stat(dest).st_mtime) == old

    def test_refuses_to_overwrite(self, cloneable):
        """Silently replacing an existing file is how a dedup tool destroys something."""
        tmp_path, source = cloneable
        dest = tmp_path / "occupied.bin"
        dest.write_bytes(b"important")
        with pytest.raises(FileExistsError):
            clone.clone_file(source, dest)
        assert dest.read_bytes() == b"important"


class TestPlatformsWithoutCloning:
    """The module is imported by core.app, so it must import everywhere.

    fcntl is Unix-only. Importing it unconditionally made dupeGuru fail to start on Windows
    altogether -- CI caught it as ten collection errors, not as a clone test failing, because
    nothing that imports core.app could load. A feature nobody on that platform can use took
    the whole application down with it.
    """

    def test_reports_unsupported_rather_than_raising_when_fcntl_is_absent(self, monkeypatch):
        monkeypatch.setattr(clone, "fcntl", None)
        monkeypatch.setattr(clone, "_clonefile", None)
        monkeypatch.setattr(clone, "_ISLINUX", True)
        assert clone.cloning_is_possible() is False

    def test_clone_file_refuses_cleanly_with_no_mechanism(self, tmp_path, monkeypatch):
        """Not AttributeError or NameError -- the caller has to be able to catch this."""
        monkeypatch.setattr(clone, "fcntl", None)
        monkeypatch.setattr(clone, "_clonefile", None)
        monkeypatch.setattr(clone, "_ISLINUX", False)
        source = tmp_path / "s.bin"
        source.write_bytes(b"x")
        with pytest.raises(clone.CloneNotSupportedError):
            clone.clone_file(source, tmp_path / "d.bin")


class TestFailedCloneLeavesNothingBehind:
    """A clone that could not be made must not leave a destination (issue #202).

    FICLONE needs the destination to exist before it can clone into it, so the Linux path
    creates the file and only then discovers whether the filesystem can do the work. On ext4,
    on XFS without reflink, or across two filesystems, it never can -- and the destination
    used to survive the failure. ``_do_delete_dupe`` builds the clone at ``<name>``
    ``.dupeguru-link`` *before* deleting anything, and its cleanup lives in the ``except``
    around the deletion, which that failure never reaches. Result: one zero-byte file beside
    every duplicate the user tried to clone, and ``_unused_link_path`` then counted around the
    litter (``.dupeguru-link1``, ``.dupeguru-link2``, ...) rather than reusing it.

    These drive the Linux branch with a stub ``fcntl`` so the unsupported case is reachable on
    a filesystem that supports cloning perfectly well.
    """

    @staticmethod
    def _fake_fcntl(exc):
        """A stand-in for the fcntl module whose ioctl always fails with *exc*."""

        class FakeFcntl:
            @staticmethod
            def ioctl(fd, request, arg):
                raise exc

        return FakeFcntl

    def _linux_with_failing_ioctl(self, monkeypatch, exc):
        monkeypatch.setattr(clone, "_clonefile", None)
        monkeypatch.setattr(clone, "_ISLINUX", True)
        monkeypatch.setattr(clone, "fcntl", self._fake_fcntl(exc))

    @pytest.mark.parametrize(
        "err",
        [errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP, errno.EINVAL],
        ids=["enotsup", "exdev", "eopnotsupp", "einval"],
    )
    def test_unsupported_filesystem_leaves_no_destination(self, tmp_path, monkeypatch, err):
        """ext4 and a cross-filesystem pair both land here, and both used to litter."""
        self._linux_with_failing_ioctl(monkeypatch, OSError(err, os.strerror(err)))
        source = tmp_path / "s.bin"
        source.write_bytes(b"A" * 4096)
        dest = tmp_path / "s.bin.dupeguru-link"

        with pytest.raises(clone.CloneNotSupportedError):
            clone.clone_file(source, dest)

        assert not dest.exists(), "a zero-byte destination was left behind"
        assert set(os.listdir(tmp_path)) == {"s.bin"}

    def test_an_unexpected_oserror_also_leaves_no_destination(self, tmp_path, monkeypatch):
        """A full disk or a permission problem is not a support problem, but still litters."""
        self._linux_with_failing_ioctl(monkeypatch, OSError(errno.ENOSPC, "No space left on device"))
        source = tmp_path / "s.bin"
        source.write_bytes(b"A" * 4096)
        dest = tmp_path / "out.bin"

        with pytest.raises(OSError) as exc:
            clone.clone_file(source, dest)
        assert not isinstance(exc.value, clone.CloneNotSupportedError)
        assert not dest.exists()

    def test_a_keyboard_interrupt_mid_clone_leaves_no_destination(self, tmp_path, monkeypatch):
        """Cleanup is on BaseException, so cancelling a long deletion does not litter either."""
        self._linux_with_failing_ioctl(monkeypatch, KeyboardInterrupt())
        source = tmp_path / "s.bin"
        source.write_bytes(b"A" * 4096)
        dest = tmp_path / "out.bin"

        with pytest.raises(KeyboardInterrupt):
            clone.clone_file(source, dest)
        assert not dest.exists()

    def test_failing_metadata_copy_leaves_no_half_made_clone(self, tmp_path, monkeypatch):
        """A destination that exists but is not a finished clone is litter too.

        The ioctl succeeds here and the chmod does not, which is the one path that leaves a
        destination with real content rather than zero bytes. Still not a clone the caller
        asked for, so it goes.
        """
        monkeypatch.setattr(clone, "_clonefile", None)
        monkeypatch.setattr(clone, "_ISLINUX", True)

        class FakeFcntl:
            @staticmethod
            def ioctl(fd, request, arg):
                os.write(fd, b"A" * 4096)  # stand in for the blocks FICLONE would share

        monkeypatch.setattr(clone, "fcntl", FakeFcntl)

        def boom(*args, **kwargs):
            raise OSError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(clone.os, "chmod", boom)

        source = tmp_path / "s.bin"
        source.write_bytes(b"A" * 4096)
        dest = tmp_path / "out.bin"

        with pytest.raises(OSError):
            clone.clone_file(source, dest)
        assert not dest.exists()

    def test_can_clone_does_not_warn_when_the_probe_was_already_cleaned(self, tmp_path, monkeypatch, caplog):
        """The regression this fix could have introduced.

        can_clone's ``finally`` unlinks its probe and warns when it cannot. Now that
        clone_file removes a destination it could not finish, that unlink raises
        FileNotFoundError on the unsupported path -- which would have logged a warning on
        every ext4 machine, for the exact case can_clone exists to detect.
        """
        err = errno.ENOTSUP
        self._linux_with_failing_ioctl(monkeypatch, OSError(err, os.strerror(err)))
        source = tmp_path / "s.bin"
        source.write_bytes(b"A" * 4096)

        with caplog.at_level(logging.WARNING):
            assert clone.can_clone(source, tmp_path) is False

        assert "clone probe" not in caplog.text
        assert set(os.listdir(tmp_path)) == {"s.bin"}


class TestSupportDetection:
    def test_can_clone_leaves_no_probe_behind(self, cloneable):
        """It works by actually cloning, so it must clean up after itself."""
        tmp_path, source = cloneable
        before = set(os.listdir(tmp_path))
        clone.can_clone(source, tmp_path)
        assert set(os.listdir(tmp_path)) == before

    def test_can_clone_is_false_across_filesystems(self, cloneable):
        """Clones share blocks within one filesystem; across devices there is nothing to share."""
        tmp_path, source = cloneable
        # /dev is a different filesystem on every platform this runs on.
        assert clone.can_clone(source, "/dev") is False

    def test_cross_filesystem_raises_the_unsupported_type_specifically(self, cloneable):
        """The *type* matters, not just that it fails.

        _make_replacement_clone catches CloneNotSupportedError to turn it into "this
        filesystem cannot clone, nothing was deleted". If the errno mapping broke, that
        became a bare OSError with a confusing message instead of a clear refusal -- and
        can_clone would still return False, so nothing else would notice.
        """
        _, source = cloneable
        with pytest.raises(clone.CloneNotSupportedError):
            clone.clone_file(source, "/dev/dupeguru-clone-probe")

    def test_missing_source_is_an_error_not_an_unsupported_report(self, tmp_path):
        """A missing file is not a support problem and must not be reported as one."""
        with pytest.raises(OSError) as exc:
            clone.clone_file(tmp_path / "nope.bin", tmp_path / "out.bin")
        assert not isinstance(exc.value, clone.CloneNotSupportedError)
