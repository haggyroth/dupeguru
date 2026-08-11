# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""One screen must not use both words for the same place (issue #215).

Windows calls it the Recycle Bin; macOS and Linux call it the Trash. Six user-facing strings
named that destination and none of them chose at runtime, so on Windows the Actions menu said
"Send Marked to Recycle Bin..." and the dialog it opened said "You are sending 3 file(s) to the
Trash." On macOS it was the other way round: the menu was wrong instead.

Every test here drives *both* platforms rather than the one it runs on. That is the whole
point -- the defect was invisible on whichever platform the developer used, and half of it was
invisible on each.
"""

import pytest

from core import trash
from hscommon import plat
from qt import deletion_preview, result_window


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(plat, "ISWINDOWS", True)


@pytest.fixture
def unix(monkeypatch):
    monkeypatch.setattr(plat, "ISWINDOWS", False)


#: Every message that names the destination, and the call that produces it. Listed once so
#: another message added later without a platform branch shows up as an omission here rather
#: than as a mismatched pair of words on someone's screen.
#:
#: Two of them live in qt/ rather than core/trash.py, and that split is deliberate: those
#: modules translate against the "ui" domain while core uses "core". Moving them for tidiness
#: would orphan their translations -- "Send Marked to Recycle Bin..." is already in ui.po in 22
#: languages, and a lookup under the core domain would find nothing.
MESSAGES = {
    "confirmation": lambda: trash.sending_files_message(3),
    "menu entry": lambda: result_window._send_marked_label(),
    "progress title": trash.sending_job_title,
    "success report": trash.all_sent_message,
    "plan verb": lambda: trash.deletion_verb(False),
    "preview outcome": lambda: deletion_preview._sent_outcome(),
    "bare noun": trash.trash_name,
}


class TestEveryMessageFollowsThePlatform:
    @pytest.mark.parametrize("name", sorted(MESSAGES))
    def test_windows_says_recycle_bin_and_never_trash(self, name, windows):
        produced = MESSAGES[name]()
        assert "Recycle Bin" in produced, f"{name}: {produced!r}"
        assert "trash" not in produced.lower(), f"{name} mixes both words: {produced!r}"

    @pytest.mark.parametrize("name", sorted(MESSAGES))
    def test_elsewhere_says_trash_and_never_recycle_bin(self, name, unix):
        produced = MESSAGES[name]()
        assert "rash" in produced, f"{name}: {produced!r}"
        assert "recycle" not in produced.lower(), f"{name} mixes both words: {produced!r}"

    @pytest.mark.parametrize("name", sorted(MESSAGES))
    def test_the_two_platforms_actually_differ(self, name, monkeypatch):
        # Catches a message that was added to the table but never given a branch: it would pass
        # one of the two tests above by accident and read wrongly on the other platform.
        monkeypatch.setattr(plat, "ISWINDOWS", True)
        on_windows = MESSAGES[name]()
        monkeypatch.setattr(plat, "ISWINDOWS", False)
        assert MESSAGES[name]() != on_windows, f"{name} says the same thing on both platforms"


class TestTheScreensAgreeWithEachOther:
    """The actual symptom: two strings shown one click apart, disagreeing."""

    @pytest.mark.parametrize("on_windows", [True, False], ids=["windows", "unix"])
    def test_the_menu_and_the_dialog_it_opens_use_the_same_word(self, monkeypatch, on_windows):
        """The exact reported sequence: Actions -> Send Marked -> confirmation dialog."""
        monkeypatch.setattr(plat, "ISWINDOWS", on_windows)
        word = "Recycle Bin" if on_windows else "Trash"
        assert word in result_window._send_marked_label()
        assert word in trash.sending_files_message(3)

    @pytest.mark.parametrize("on_windows", [True, False], ids=["windows", "unix"])
    def test_the_dialog_and_its_preview_use_the_same_word(self, monkeypatch, on_windows):
        # ...and on into the preview. summarize_plan feeds both that and the command line.
        monkeypatch.setattr(plat, "ISWINDOWS", on_windows)
        word = "recycle bin" if on_windows else "trash"
        assert word in trash.sending_files_message(3).lower()
        assert word in trash.deletion_verb(False).lower()
        assert word in deletion_preview._sent_outcome().lower()

    @pytest.mark.parametrize("on_windows", [True, False], ids=["windows", "unix"])
    def test_no_two_messages_disagree(self, monkeypatch, on_windows):
        """The invariant, rather than a walk through one path: on either platform, every
        message uses that platform's word and none of them uses the other."""
        monkeypatch.setattr(plat, "ISWINDOWS", on_windows)
        ours, theirs = ("recycle bin", "trash") if on_windows else ("trash", "recycle bin")
        for name, produce in MESSAGES.items():
            text = produce().lower()
            assert ours in text, f"{name} does not use {ours!r}: {text!r}"
            assert theirs not in text, f"{name} still uses {theirs!r}: {text!r}"


class TestItStaysTranslatable:
    def test_the_messages_are_whole_sentences_not_assembled_ones(self):
        """A name slotted into a sentence is what a translator cannot work with.

        In languages that inflect, the article and case of "Trash" depend on the phrase around
        it, and `tr("...to the {}.")` gives the translator no way to see or vary that. Each
        message is therefore its own complete string per platform. This asserts the shape that
        makes that true: no message is built by substituting a bare noun into another.
        """
        import inspect

        source = inspect.getsource(trash.sending_files_message)
        assert "trash_name" not in source, "the confirmation is assembled from the bare noun"
        # {} is the file count, which is a number and safe to substitute.
        assert source.count("{}") == 2, "expected one placeholder per platform branch"

    def test_the_count_is_still_substituted(self):
        assert "7" in trash.sending_files_message(7)

    @pytest.mark.parametrize("on_windows", [True, False], ids=["windows", "unix"])
    def test_permanent_deletion_is_unaffected(self, monkeypatch, on_windows):
        # Nothing about a permanent delete involves either name, on any platform.
        monkeypatch.setattr(plat, "ISWINDOWS", on_windows)
        verb = trash.deletion_verb(True)
        assert verb == "permanently delete"
        assert "recycle" not in verb.lower() and "trash" not in verb.lower()


def _code_of(path):
    """A file's source with comment lines dropped.

    The comments deliberately name both words while explaining the bug, so scanning them would
    report the explanation as the defect.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


class TestTheCallSitesUseThem:
    """Guards against a site drifting back to one hardcoded word.

    Two shapes are correct, and the difference is the translation domain. A core-domain caller
    delegates and should name neither destination; the two Qt modules translate against "ui",
    so they hold their own branch and must name *both*. Naming exactly one is the bug.
    """

    NAMES = ("Recycle Bin", "to the Trash", "to Trash", "sent to trash", "send to trash")

    @pytest.mark.parametrize(
        "path",
        ["core/gui/deletion_options.py", "core/app.py", "core/deletion_plan.py"],
    )
    def test_a_delegating_site_names_neither(self, path):
        code = _code_of(path)
        for word in self.NAMES:
            assert word not in code, f"{path} hardcodes {word!r} instead of asking core.trash"

    @pytest.mark.parametrize("path", ["qt/result_window.py", "qt/deletion_preview.py"])
    def test_a_branching_site_names_both(self, path):
        # Half a branch is the original bug. These live in the "ui" domain, so they cannot
        # delegate to core/trash.py without orphaning their translations.
        code = _code_of(path)
        assert "Recycle Bin" in code, f"{path} lost its Windows wording"
        assert "Trash" in code or "trash" in code, f"{path} lost its non-Windows wording"
        assert "plat.ISWINDOWS" in code, f"{path} names both words but never chooses between them"


class TestThePlatformIsReadLate:
    """Each module must consult `plat.ISWINDOWS` at call time, not bind it at import.

    `from hscommon.plat import ISWINDOWS` would copy the value once, which works in production
    -- the platform does not change -- but makes every test above untestable on one platform
    and would quietly reduce this suite to asserting the host's own behaviour twice.
    """

    @pytest.mark.parametrize("path", ["core/trash.py", "qt/result_window.py", "qt/deletion_preview.py"])
    def test_the_module_does_not_bind_iswindows_by_value(self, path):
        code = _code_of(path)
        assert "from hscommon.plat import" not in code, f"{path} binds the platform at import time"
        assert "plat.ISWINDOWS" in code, f"{path} never reads the platform through the module"
