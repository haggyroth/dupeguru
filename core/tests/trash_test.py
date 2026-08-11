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
