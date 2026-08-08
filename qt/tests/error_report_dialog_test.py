# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The crash reporter.

This is what appears when something has already gone wrong, which makes it the worst place for
a second fault: a broken reporter turns a visible crash into a silent one, and the user is left
with an application behaving oddly and nothing to send anybody.

It had no coverage at all. What matters is that the report actually contains the traceback and
enough context to act on, that the hook is installed so unhandled exceptions reach it, and that
"Go to GitHub" goes to *this* fork -- crash reports were pointed away from upstream deliberately,
and nothing was checking that they stayed pointed here.
"""

import sys

import pytest

pytest.importorskip("qtpy.QtWidgets", reason="no Qt bindings installed")

from qtpy.QtCore import QCoreApplication  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from qt.error_report_dialog import ErrorReportDialog, install_excepthook  # noqa: E402

FORK_URL = "https://github.com/haggyroth/dupeguru/issues"

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "core/app.py", line 42, in _do_delete_dupe\n'
    "    raise OSError('a distinctive failure')\n"
    "OSError: a distinctive failure\n"
)


@pytest.fixture
def dialog(qapp):
    dialog = ErrorReportDialog(None, FORK_URL, TRACEBACK)
    yield dialog
    dialog.close()


class TestTheReport:
    def test_the_traceback_is_in_the_report(self, dialog):
        # Without this the dialog is an apology with no information in it.
        text = dialog.errorTextEdit.toPlainText()
        assert "OSError: a distinctive failure" in text
        assert "_do_delete_dupe" in text

    def test_the_report_carries_enough_context_to_act_on(self, dialog):
        # A traceback alone rarely identifies the build it came from.
        text = dialog.errorTextEdit.toPlainText()
        assert "Version:" in text
        assert "Python:" in text
        assert "Operating System:" in text
        assert sys.version.split()[0] in text

    def test_the_report_names_the_application(self, dialog):
        assert QCoreApplication.applicationName() in dialog.errorTextEdit.toPlainText()

    def test_the_report_cannot_be_edited(self, dialog):
        # It is evidence; letting it be typed over invites reports that do not match reality.
        assert dialog.errorTextEdit.isReadOnly()

    def test_line_endings_do_not_mangle_the_traceback(self, dialog):
        # The text is rewritten with os.linesep for Windows' benefit. That must not run the
        # traceback's lines together, which would make it unreadable in an issue.
        text = dialog.errorTextEdit.toPlainText()
        assert "OSError" in text
        lines = [line for line in text.splitlines() if line.strip()]
        assert len(lines) >= 6, "the report should still be several lines"


class TestActions:
    def test_copy_puts_the_whole_report_on_the_clipboard(self, qapp, dialog):
        # The instructions tell the user to paste this into an issue, so a partial copy is a
        # report nobody can act on.
        QApplication.clipboard().clear()
        dialog.copyToClipboard()
        assert QApplication.clipboard().text() == dialog.errorTextEdit.toPlainText()

    def test_go_to_github_opens_the_configured_url(self, dialog, monkeypatch):
        opened = []
        monkeypatch.setattr("qt.error_report_dialog.open_url", opened.append)
        dialog.goToGitHub()
        assert opened == [FORK_URL]

    def test_the_url_is_whatever_the_caller_passed(self, qapp, monkeypatch):
        # Crash reports were deliberately pointed at this fork rather than upstream, where the
        # issue numbers mean different things and the maintainers did not write this code.
        other = ErrorReportDialog(None, "https://example.invalid/issues", TRACEBACK)
        opened = []
        monkeypatch.setattr("qt.error_report_dialog.open_url", opened.append)
        other.goToGitHub()
        assert opened == ["https://example.invalid/issues"]
        other.close()

    def test_closing_without_reporting_is_offered(self, dialog):
        assert dialog.dontSendButton.text() == "Close"
        assert dialog.sendButton.isDefault(), "reporting should be the default action"


class TestExceptHook:
    @pytest.fixture(autouse=True)
    def restore_excepthook(self):
        original = sys.excepthook
        yield
        sys.excepthook = original

    def test_installing_replaces_the_excepthook(self):
        before = sys.excepthook
        install_excepthook(FORK_URL)
        assert sys.excepthook is not before

    def test_an_unhandled_exception_reaches_a_report(self, qapp, monkeypatch):
        # The whole point of the module. Driving the hook directly rather than raising, because
        # the real one calls exec() and would block the suite forever.
        built = {}

        class StubDialog:
            def __init__(self, parent, github_url, error):
                built["url"] = github_url
                built["error"] = error

            def exec(self):
                built["shown"] = True

        monkeypatch.setattr("qt.error_report_dialog.ErrorReportDialog", StubDialog)
        install_excepthook(FORK_URL)

        try:
            raise ValueError("something distinctive went wrong")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        assert built["shown"] is True
        assert built["url"] == FORK_URL
        assert "something distinctive went wrong" in built["error"]
        assert "ValueError" in built["error"]

    def test_the_crash_is_logged_as_well_as_shown(self, qapp, monkeypatch, caplog):
        # The dialog can be dismissed; the log is what survives to be read afterwards.
        monkeypatch.setattr(
            "qt.error_report_dialog.ErrorReportDialog",
            lambda parent, github_url, error: type("D", (), {"exec": lambda self: None})(),
        )
        install_excepthook(FORK_URL)

        with caplog.at_level("CRITICAL"):
            try:
                raise RuntimeError("logged too")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())

        assert any("logged too" in record.getMessage() for record in caplog.records)
