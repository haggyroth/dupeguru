# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The system-location warning as the two front ends actually deliver it (issue #134).

Which locations count is settled in sensitive_paths_test.py. What is settled here is the part
the issue is anxious about: that the prompt stays out of the way. It must not fire on ordinary
folders, and it must not fire again for a folder already agreed to -- re-scanning while
adjusting filters is normal, and a prompt on every pass is how a warning becomes something
people click through without reading. That habit would carry to the multi-drive and
partial-hash prompts, which guard real data loss.

It also must not *refuse*. Cleaning an application-support directory is a real thing to want to
do, so the GUI asks and the command line merely says so.
"""

from pathlib import Path

import pytest

from core import app as core_app
from core import fs, sensitive_paths
from core.hash_cache import hashcachedb
from core.tests.base import TestApp


@pytest.fixture
def scanning_app(monkeypatch):
    """An app whose scan is spied on rather than run, as in TestStartScanning."""
    dgapp = TestApp().app
    started = []
    dgapp._start_job = lambda *a, **k: started.append(a)
    dgapp.directories.has_any_file = lambda: True
    monkeypatch.setattr(core_app.DupeGuru, "_dirs_span_multiple_devices", staticmethod(lambda d: False))
    monkeypatch.setattr(fs.filesdb, "purge_if_stale", lambda: None)
    monkeypatch.setattr(hashcachedb, "purge_if_stale", lambda: None)
    return dgapp, started


def add_sensitive(dgapp, monkeypatch, *paths):
    """Put *paths* in the app's directory list and make them read as sensitive.

    The reason is stubbed rather than pointing the app at a real system folder: adding
    ``/System`` to a test's directory list makes the test depend on the machine it runs on, and
    the platform lists are already covered on their own.
    """
    monkeypatch.setattr(
        sensitive_paths, "reason_for", lambda path: "a stubbed reason" if Path(path) in set(paths) else ""
    )
    for path in paths:
        dgapp.directories._dirs.append(Path(path))


class TestTheGuiPrompt:
    def test_an_ordinary_folder_asks_nothing(self, scanning_app, tmp_path):
        # The test the feature lives or dies by: silence on the folders people actually scan.
        dgapp, started = scanning_app
        dgapp.directories._dirs.append(tmp_path)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert asked == []
        assert started, "an ordinary scan must not be held up"

    def test_a_sensitive_folder_asks_first(self, scanning_app, monkeypatch, tmp_path):
        dgapp, started = scanning_app
        add_sensitive(dgapp, monkeypatch, tmp_path)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert len(asked) == 1
        assert str(tmp_path) in asked[0]
        assert "a stubbed reason" in asked[0]
        assert started, "the scan should proceed once the user accepts"

    def test_declining_stops_the_scan(self, scanning_app, monkeypatch, tmp_path):
        dgapp, started = scanning_app
        add_sensitive(dgapp, monkeypatch, tmp_path)
        dgapp.view.ask_yes_no = lambda prompt: False

        dgapp.start_scanning()

        assert not started, "the scan started despite the user declining"

    def test_accepting_is_remembered_for_the_session(self, scanning_app, monkeypatch, tmp_path):
        # The anti-fatigue rule. Re-scanning the same folder while tuning filters is normal.
        dgapp, started = scanning_app
        add_sensitive(dgapp, monkeypatch, tmp_path)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()
        dgapp.start_scanning()
        dgapp.start_scanning()

        assert len(asked) == 1, "the same folder was raised more than once"
        assert len(started) == 3, "every scan should still have run"

    def test_declining_is_not_remembered(self, scanning_app, monkeypatch, tmp_path):
        # Only agreement is remembered. Treating a "no" as a standing answer would silently
        # stop later scans the user did mean to run.
        dgapp, started = scanning_app
        add_sensitive(dgapp, monkeypatch, tmp_path)
        answers = [False, True]
        dgapp.view.ask_yes_no = lambda prompt: answers.pop(0)

        dgapp.start_scanning()
        dgapp.start_scanning()

        assert answers == [], "the second scan did not ask again"
        assert len(started) == 1

    def test_a_newly_added_sensitive_folder_is_raised_on_its_own(self, scanning_app, monkeypatch, tmp_path):
        # Agreeing to one folder says nothing about the next one.
        dgapp, started = scanning_app
        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        add_sensitive(dgapp, monkeypatch, first)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()
        add_sensitive(dgapp, monkeypatch, first, second)
        dgapp.start_scanning()

        assert len(asked) == 2
        assert str(second) in asked[1]
        assert str(first) not in asked[1], "a folder already agreed to was raised again"

    def test_the_prompt_asks_rather_than_forbids(self, scanning_app, monkeypatch, tmp_path):
        dgapp, _ = scanning_app
        add_sensitive(dgapp, monkeypatch, tmp_path)
        asked = []
        dgapp.view.ask_yes_no = lambda prompt: asked.append(prompt) or True

        dgapp.start_scanning()

        assert asked[0].rstrip().endswith("?")


class TestTheCommandLine:
    """A scan deletes nothing, so the command line says it rather than asking."""

    def _run(self, folder, capsys):
        from cli import main

        rc = main([str(folder)])
        return rc, capsys.readouterr().err

    def test_an_ordinary_folder_warns_about_nothing(self, tmp_path, capsys):
        (tmp_path / "a.txt").write_bytes(b"same")
        (tmp_path / "b.txt").write_bytes(b"same")
        _, err = self._run(tmp_path, capsys)
        assert "operating system" not in err

    def test_a_sensitive_folder_is_named_with_its_reason(self, tmp_path, capsys, monkeypatch):
        (tmp_path / "a.txt").write_bytes(b"same")
        (tmp_path / "b.txt").write_bytes(b"same")
        monkeypatch.setattr(sensitive_paths, "reason_for", lambda path: "a stubbed reason")
        _, err = self._run(tmp_path, capsys)
        assert "a stubbed reason" in err
        assert str(tmp_path) in err

    def test_the_warning_does_not_stop_the_scan(self, tmp_path, capsys, monkeypatch):
        # Refusing here would break scripted cleanups of application-support directories, and
        # a scan removes nothing anyway -- --delete has its own confirmation.
        from cli import EXIT_DUPES_FOUND

        (tmp_path / "a.txt").write_bytes(b"same")
        (tmp_path / "b.txt").write_bytes(b"same")
        monkeypatch.setattr(sensitive_paths, "reason_for", lambda path: "a stubbed reason")
        rc, _ = self._run(tmp_path, capsys)
        assert rc == EXIT_DUPES_FOUND

    def test_every_line_of_the_warning_is_marked_as_one(self, tmp_path, capsys, monkeypatch):
        # The message runs to several lines. An unprefixed continuation line in a terminal full
        # of scan output does not read as part of the warning.
        (tmp_path / "a.txt").write_bytes(b"same")
        (tmp_path / "b.txt").write_bytes(b"same")
        monkeypatch.setattr(sensitive_paths, "reason_for", lambda path: "a stubbed reason")
        _, err = self._run(tmp_path, capsys)
        warning_block = [line for line in err.splitlines() if "stubbed" in line or "installed software" in line]
        assert warning_block, "the warning did not appear at all"
        assert all(line.startswith("warning:") for line in warning_block)
