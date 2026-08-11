# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Capturing where a trashed file went (issue #125).

The Windows path is exercised here with stand-ins for pywin32 and send2trash's internals,
because the interesting behaviour -- swapping the sink factory, restoring it afterwards,
reading the captured item back -- is ours, and because it would otherwise only ever be
verified on one CI leg. The real thing is covered by the round-trip test in
core/tests/deletion_log_test.py, which runs on every platform.
"""

import os
import sys
import types

import pytest

from core import trash


class FakeSink:
    """Stands in for send2trash's IFileOperationProgressSink implementation."""

    def __init__(self):
        self.newItem = None


def install_fake_windows(monkeypatch, on_delete, sink_factory=FakeSink):
    """Make core.trash's Windows imports resolve to fakes, and return the modern module."""
    modern = types.ModuleType("send2trash.win.modern")

    def original_create_sink():
        return "the original wrapped sink"

    modern.create_sink = original_create_sink

    sink_module = types.ModuleType("send2trash.win.IFileOperationProgressSink")
    sink_module.FileOperationProgressSink = sink_factory

    win_pkg = types.ModuleType("send2trash.win")
    win_pkg.modern = modern

    pythoncom = types.ModuleType("pythoncom")
    pythoncom.WrapObject = lambda obj, iid: ("wrapped", obj)

    shell = types.ModuleType("win32com.shell.shell")
    shell.IID_IFileOperationProgressSink = "iid"
    shell_pkg = types.ModuleType("win32com.shell")
    shell_pkg.shell = shell
    win32com = types.ModuleType("win32com")
    win32com.shell = shell_pkg

    for name, module in [
        ("send2trash.win", win_pkg),
        ("send2trash.win.modern", modern),
        ("send2trash.win.IFileOperationProgressSink", sink_module),
        ("pythoncom", pythoncom),
        ("win32com", win32com),
        ("win32com.shell", shell_pkg),
        ("win32com.shell.shell", shell),
    ]:
        monkeypatch.setitem(sys.modules, name, module)

    # send2trash's own deletion is left alone by design; this stands in for it and lets the
    # test drive what the sink saw.
    monkeypatch.setattr(trash, "send2trash", on_delete)
    return modern


class TestWindowsCapture:
    def test_the_destination_is_read_from_the_sink(self, monkeypatch):
        captured = {}

        def fake_send2trash(path):
            # send2trash calls create_sink() while deleting; do the same so the sink the
            # factory handed out is the one that gets its newItem set.
            sink = sys.modules["send2trash.win.modern"].create_sink()
            captured["handed_out"] = sink
            sink[1].newItem = r"C:\$Recycle.Bin\S-1-5-21-1\$RABCDEF.txt"

        install_fake_windows(monkeypatch, fake_send2trash)
        assert trash._trash_windows(r"C:\photos\a.txt") == r"C:\$Recycle.Bin\S-1-5-21-1\$RABCDEF.txt"

    def test_the_factory_is_put_back_afterwards(self, monkeypatch):
        # The swap is global to send2trash's module. Leaving it in place would make every later
        # deletion in the process run through our factory, including ones we never asked about.
        modern = install_fake_windows(monkeypatch, lambda path: None)
        before = modern.create_sink
        trash._trash_windows(r"C:\photos\a.txt")
        assert modern.create_sink is before

    def test_the_factory_is_put_back_even_when_the_deletion_fails(self, monkeypatch):
        def failing(path):
            raise OSError("access denied")

        modern = install_fake_windows(monkeypatch, failing)
        before = modern.create_sink
        with pytest.raises(OSError):
            trash._trash_windows(r"C:\photos\a.txt")
        assert modern.create_sink is before

    def test_a_failure_to_recycle_propagates(self, monkeypatch):
        # The caller treats this as the file not having been deleted, which is true. Swallowing
        # it and retrying could delete twice.
        install_fake_windows(monkeypatch, lambda path: (_ for _ in ()).throw(OSError("nope")))
        with pytest.raises(OSError):
            trash._trash_windows(r"C:\photos\a.txt")

    def test_a_sink_that_reports_nothing_yields_no_destination(self, monkeypatch):
        # The file is still recycled; only the undo is unavailable. Returning a guess here
        # would be worse than returning nothing.
        install_fake_windows(monkeypatch, lambda path: None)
        assert trash._trash_windows(r"C:\photos\a.txt") == ""

    def test_missing_pywin32_still_recycles_the_file(self, monkeypatch):
        deleted = []
        monkeypatch.setitem(sys.modules, "pythoncom", None)
        monkeypatch.setattr(trash, "send2trash", deleted.append)
        # None in sys.modules makes `import pythoncom` raise ImportError.
        assert trash._trash_windows(r"C:\photos\a.txt") == ""
        assert deleted == [r"C:\photos\a.txt"], "the file must still be recycled"


class _FakeFunc:
    """A stand-in for a ctypes function pointer, with settable restype/argtypes."""

    def __init__(self, behaviour=0):
        self.behaviour = behaviour
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self.behaviour(*args) if callable(self.behaviour) else self.behaviour


class _FakeLib:
    """A stand-in for a loaded dylib; every symbol resolves to a _FakeFunc returning 0."""

    def __init__(self, **behaviours):
        self.__dict__["_behaviours"] = behaviours
        self.__dict__["_cache"] = {}

    def __getattr__(self, name):
        cache = self.__dict__["_cache"]
        if name not in cache:
            cache[name] = _FakeFunc(self.__dict__["_behaviours"].get(name, 0))
        return cache[name]


def install_fake_coreservices(monkeypatch, **behaviours):
    """Make _trash_macos talk to a fake CoreServices.

    The ctypes imports happen inside the function on every call, so patching the modules is
    enough. This lets the *real* split-at-the-move logic be driven on any platform, and without
    trashing files whose destination the test has just arranged to be unreadable -- which it
    would then be unable to clean up.
    """
    import ctypes
    import ctypes.util

    core_services = _FakeLib(**behaviours)
    foundation = _FakeLib(GetMacOSStatusCommentString=lambda status: b"simulated CoreServices error")

    monkeypatch.setattr(ctypes.util, "find_library", lambda name: name)
    monkeypatch.setattr(
        ctypes.cdll,
        "LoadLibrary",
        lambda name: foundation if name == "Foundation" else core_services,
    )
    return core_services


class TestTheMoveIsTheDividingLine:
    """Nothing after FSMoveObjectToTrashSync may raise or report failure (issue #201).

    The bug: a failure *after* the move returned "", which `trash_file` could not tell from
    "this path did not run" -- so it fell through to send2trash with a path that no longer
    existed. That raised, and the application reported a failed deletion for a file that was
    already in the trash. The deletion record is written after `trash_file` returns, so there
    was no log entry either: the file was gone, the user was told it survived, and there was
    nothing to restore from.

    So the three outcomes have to be distinguishable, and these pin each one.
    """

    def test_a_failure_before_the_move_says_nothing_was_trashed(self, monkeypatch):
        """A missing symbol or a ctypes problem, i.e. the case this path was always allowed to
        decline. None, not "": nothing moved, so the caller must fall back to send2trash."""
        import ctypes
        import ctypes.util

        def cannot_load(name):
            raise RuntimeError("no CoreServices on this system")

        monkeypatch.setattr(ctypes.util, "find_library", lambda name: name)
        monkeypatch.setattr(ctypes.cdll, "LoadLibrary", cannot_load)
        monkeypatch.setattr(trash.logging, "warning", lambda *a, **k: None)
        assert trash._trash_macos("/some/file") is None

    def test_a_bad_status_before_the_move_propagates(self, monkeypatch):
        # The file could not even be located. Raising is correct: it was not deleted.
        install_fake_coreservices(monkeypatch, FSPathMakeRefWithOptions=-43)
        with pytest.raises(OSError):
            trash._trash_macos("/some/file")

    def test_a_bad_status_from_the_move_propagates(self, monkeypatch):
        # The move itself refused, so the file is still there and the caller must hear about it.
        install_fake_coreservices(monkeypatch, FSMoveObjectToTrashSync=-120)
        with pytest.raises(OSError):
            trash._trash_macos("/some/file")

    def test_an_unreadable_destination_still_says_the_file_was_trashed(self, monkeypatch):
        """The bug, at its source. The move succeeded; reading the location did not."""
        install_fake_coreservices(monkeypatch, FSMoveObjectToTrashSync=0, FSRefMakePath=-1)
        monkeypatch.setattr(trash.logging, "warning", lambda *a, **k: None)
        assert trash._trash_macos("/some/file") == "", "a post-move failure must not read as 'not trashed'"

    def test_a_destination_that_is_not_utf8_is_dropped_rather_than_escaped(self, monkeypatch):
        """Deliberately lossy, and the reason is worth keeping.

        errors="surrogateescape" looks like the better answer -- surrogates round-trip through
        the filesystem, so the restore would still work. But the destination is stored in the
        deletion log as XML, and ElementTree writes a lone surrogate as a character reference
        that is not valid XML: the next load() fails to parse the file and silently drops
        *every* run in it. One undecodable path would take the whole undo history with it.
        """

        def write_invalid_utf8(ref, buf, size):
            buf.value = b"/Users/x/.Trash/caf\xe9.txt"
            return 0

        install_fake_coreservices(monkeypatch, FSMoveObjectToTrashSync=0, FSRefMakePath=write_invalid_utf8)
        monkeypatch.setattr(trash.logging, "warning", lambda *a, **k: None)
        result = trash._trash_macos("/some/file")
        assert result == ""
        assert "\udce9" not in result, "a surrogate would corrupt the deletion log on save"

    def test_an_unknowable_move_is_reported_rather_than_retried(self, monkeypatch):
        """If we cannot tell whether the file moved, retrying could delete a second one."""

        def explode(*args):
            raise RuntimeError("ctypes went wrong mid-move")

        install_fake_coreservices(monkeypatch, FSMoveObjectToTrashSync=explode)
        with pytest.raises(OSError) as caught:
            trash._trash_macos("/some/file")
        assert "could not tell" in str(caught.value).lower()

    def test_a_readable_destination_comes_back_intact(self, monkeypatch):
        def write_path(ref, buf, size):
            buf.value = b"/Users/x/.Trash/a.txt"
            return 0

        install_fake_coreservices(monkeypatch, FSMoveObjectToTrashSync=0, FSRefMakePath=write_path)
        assert trash._trash_macos("/some/file") == "/Users/x/.Trash/a.txt"


@pytest.mark.skipif(sys.platform != "darwin", reason="trash_file dispatches to CoreServices on macOS only")
class TestTrashFileHonoursTheDistinction:
    """The consequence of the above, one level up: `""` must not trigger a second deletion."""

    def test_a_trashed_file_with_no_destination_is_not_handed_to_send2trash(self, monkeypatch):
        # The exact sequence behind #201. send2trash on an already-trashed path raises, and the
        # application reports a failure for a deletion that happened.
        monkeypatch.setattr(trash, "_trash_macos", lambda p: "")
        called = []
        monkeypatch.setattr(trash, "send2trash", called.append)
        assert trash.trash_file("/some/file") == ""
        assert called == [], "the file was already trashed; deleting it again was attempted"

    def test_a_file_that_was_not_trashed_does_fall_back(self, monkeypatch):
        monkeypatch.setattr(trash, "_trash_macos", lambda p: None)
        called = []
        monkeypatch.setattr(trash, "send2trash", called.append)
        assert trash.trash_file("/some/file") == ""
        assert called == ["/some/file"], "nothing was trashed, so the fallback must run"

    def test_a_destination_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(trash, "_trash_macos", lambda p: "/Users/x/.Trash/a.txt")
        monkeypatch.setattr(trash, "send2trash", lambda p: pytest.fail("should not be reached"))
        assert trash.trash_file("/some/file") == "/Users/x/.Trash/a.txt"


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreServices trashing is macOS-only")
class TestMacOSCapture:
    """The real thing, on the one platform that can run it.

    Unlike the Windows tests above there is nothing to stand in for: FSMoveObjectToTrashSync
    either moves the file or it does not, and the destination it reports is only meaningful if
    something is actually there. So these trash real files and put them back.

    None of this can observe the buffer bug it was written alongside -- writing into a bytes
    object produces the right answer right up until CPython decides otherwise. What it does
    cover is that the buffer is read back correctly at all, which is what makes replacing it
    safe to do.
    """

    @staticmethod
    def trashed(tmp_path, name, data=b"x"):
        """Trash a file and yield where it went, removing it afterwards."""
        source = tmp_path / name
        source.write_bytes(data)
        destination = trash._trash_macos(str(source))
        return source, destination

    def test_the_destination_is_reported_and_real(self, tmp_path):
        source, destination = self.trashed(tmp_path, "trash-probe.txt")
        try:
            assert destination, "the destination was not captured"
            assert os.path.exists(destination), f"nothing at the reported destination {destination!r}"
            assert not source.exists(), "the file was not trashed"
        finally:
            if destination and os.path.exists(destination):
                os.remove(destination)

    def test_a_non_ascii_name_survives_the_round_trip(self, tmp_path):
        # The buffer holds UTF-8 bytes, so a multi-byte name is where a truncation or a decode
        # done at the wrong width would show up. The trash may rename on collision, but never
        # transliterates, so the name itself has to come back intact.
        name = "trash-probe-é你好.txt"
        source, destination = self.trashed(tmp_path, name)
        try:
            assert destination, "the destination was not captured"
            assert os.path.exists(destination)
            assert "é你好" in os.path.basename(destination)
        finally:
            if destination and os.path.exists(destination):
                os.remove(destination)

    def test_the_reported_path_stops_at_the_terminator(self, tmp_path):
        # The buffer is 1024 bytes and the path is far shorter, so everything past it is NUL.
        # Reading the whole buffer would return a path with a tail of zeros that exists()
        # would reject -- the assertion above would catch it, but this says why.
        source, destination = self.trashed(tmp_path, "trash-probe-terminator.txt")
        try:
            assert "\0" not in destination
            assert destination == destination.rstrip("\0")
        finally:
            if destination and os.path.exists(destination):
                os.remove(destination)


class TestPlatformReporting:
    def test_every_supported_platform_claims_it_can_report(self):
        # All three capture the destination now. A platform added later must decide
        # deliberately rather than inheriting a default.
        assert trash.can_report_destination() is True

    def test_an_unknown_platform_reports_nothing_rather_than_guessing(self, monkeypatch):
        monkeypatch.setattr(trash.sys, "platform", "somethingelse")
        assert trash.can_report_destination() is False

    def test_an_unknown_platform_still_trashes(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(trash.sys, "platform", "somethingelse")
        monkeypatch.setattr(trash, "send2trash", deleted.append)
        assert trash.trash_file("/some/file") == ""
        assert deleted == ["/some/file"]
