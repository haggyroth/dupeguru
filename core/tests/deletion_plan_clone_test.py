# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A plan can report that a duplicate would be cloned rather than removed (issue #214).

``build_plan`` accepted a ``clone_probe`` and never called it. ``cloneable`` was initialised to
0 and returned unchanged, so three things downstream were unreachable in every front end:

- no plan entry ever carried ``"cloneable": True``;
- the summary line "N could be replaced by a copy-on-write clone instead of being removed"
  could not be produced;
- ``qt/deletion_preview.py``'s "replaced by a clone of the reference" was dead code.

It was not a regression -- the same defect sits at v4.18.0 -- and it went unnoticed because
``build_plan`` had no direct tests at all. The only route to it was the CLI's ``--plan``, and
the CLI never passes a probe: it is the GUI that does, when the user chooses to replace
duplicates with clones.

The consequence worth recording is what it did to the #157 Windows checklist. That list asks
someone to confirm nothing offers copy-on-write cloning where it is unavailable. Windows passed
-- but it would have passed with ``cloning_is_possible()`` broken, because nothing could report
cloning anywhere. The check could not fail on any platform, including APFS where cloning
genuinely works.
"""

import pytest

import cli
from core.app import AppMode, DupeGuru
from core.clone import can_clone
from core.deletion_plan import build_plan, default_clone_probe, summarize_plan
from core.scanner import ScanType


@pytest.fixture
def cloning_filesystem(tmp_path):
    """Skip unless *this* filesystem can actually make clones.

    Not ``cloning_is_possible()``, which answers a different question: whether the *platform*
    has a mechanism at all. Linux always does, and CI runs on ext4, which cannot reflink -- so
    gating on it ran the real-probe tests where the probe correctly refuses everything, and
    they failed on every Linux leg.

    ``core/tests/clone_test.py`` already says this in as many words -- "platform support is not
    filesystem support ... probing is the only honest answer" -- which is what the probe below
    does, and what ``default_clone_probe`` itself does.
    """
    source = tmp_path / "clone-support-probe.bin"
    source.write_bytes(b"A" * 4096)
    supported = can_clone(source, tmp_path)
    source.unlink()
    if not supported:
        pytest.skip("this filesystem cannot make copy-on-write clones")


@pytest.fixture
def planned(tmp_path):
    """An app holding a scanned, fully marked group of identical files."""

    def build(count=3, content=b"identical payload" * 20):
        folder = tmp_path / "files"
        folder.mkdir(exist_ok=True)
        for i in range(count):
            (folder / f"f{i}.bin").write_bytes(content)
        app = DupeGuru(view=cli._HeadlessView())
        app.app_mode = AppMode.STANDARD
        app.options["scan_type"] = ScanType.CONTENTS
        app.directories.add_path(folder)
        cli._run_scan(app, verbose=False)
        app.results.mark_all()
        return app

    return build


def accepts(dupe, ref):
    return True


def declines(dupe, ref):
    return False


def entries_of(plan):
    return [dupe for entry in plan.entries for dupe in entry["duplicates"]]


class TestTheProbeIsActuallyAsked:
    def test_an_accepted_file_is_counted_and_flagged(self, planned):
        app = planned()
        plan = build_plan(app, clone_probe=accepts)
        assert plan.cloneable == plan.files > 0, "the probe was accepted but nothing was counted"
        assert all(dupe["cloneable"] for dupe in entries_of(plan) if dupe["would_delete"])

    def test_a_declined_file_is_not_counted(self, planned):
        app = planned()
        plan = build_plan(app, clone_probe=declines)
        assert plan.cloneable == 0
        assert not any("cloneable" in dupe for dupe in entries_of(plan))

    def test_the_probe_is_called_once_per_deletable_candidate(self, planned):
        app = planned()
        calls = []
        build_plan(app, clone_probe=lambda dupe, ref: calls.append((dupe, ref)) or True)
        assert len(calls) == 2, f"expected one call per duplicate, got {len(calls)}"

    def test_the_probe_receives_the_dupe_and_then_its_reference(self, planned):
        # Argument order is load-bearing: default_clone_probe clones the *reference* over the
        # duplicate, so swapping them would test the wrong direction and could report a file
        # as clonable when the replacement would fail.
        app = planned()
        seen = []
        build_plan(app, clone_probe=lambda dupe, ref: seen.append((dupe, ref)) or True)
        group = app.results.groups[0]
        for dupe, ref in seen:
            assert ref is group.ref
            assert dupe is not group.ref
            assert dupe in group.dupes

    def test_no_probe_means_the_plan_never_mentions_cloning(self, planned):
        # The default, and what the CLI always does. Answering costs a real filesystem test per
        # candidate, so it must not happen unless asked for.
        app = planned()
        plan = build_plan(app)
        assert plan.cloneable == 0
        assert not any("cloneable" in dupe for dupe in entries_of(plan))

    def test_a_blocked_candidate_is_never_probed(self, planned):
        # The probe answers "could this be replaced by a clone instead of being deleted", which
        # is meaningless for a file that would not be deleted at all -- and it costs real I/O.
        app = planned()
        for dupe in app.results.groups[0].dupes:
            dupe.path.unlink()
        calls = []
        plan = build_plan(app, clone_probe=lambda dupe, ref: calls.append(1) or True)
        assert plan.files == 0, "the setup did not actually block the candidates"
        assert calls == [], "a file that would not be deleted was probed anyway"


class TestWhatTheUserIsTold:
    def test_the_summary_reports_the_count(self, planned):
        app = planned()
        lines = summarize_plan(build_plan(app, clone_probe=accepts))
        assert any("clone" in line for line in lines), lines

    def test_the_summary_stays_silent_with_no_probe(self, planned):
        app = planned()
        lines = summarize_plan(build_plan(app))
        assert not any("clone" in line for line in lines), lines

    def test_the_preview_describes_a_clone_rather_than_a_deletion(self, planned):
        """End to end into the Qt wording, which was unreachable before.

        A clone leaves both files in place, so describing it as "sent to trash" would tell the
        user the opposite of what happens.
        """
        from qt.deletion_preview import _outcome

        app = planned()
        plan = build_plan(app, clone_probe=accepts)
        described = [_outcome(dupe, direct_delete=False) for dupe in entries_of(plan)]
        assert described, "no candidates to describe"
        for outcome in described:
            assert "clone" in outcome
            assert "trash" not in outcome


class TestTheRealProbe:
    """default_clone_probe against a filesystem that genuinely clones.

    Everything above uses a stand-in, which proves the wiring but not that the shipped probe
    ever says yes. These run on APFS and Btrfs and skip on ext4, HFS+ and exFAT -- see the
    ``cloning_filesystem`` fixture for why that has to be probed rather than assumed.
    """

    def test_identical_files_are_reported_as_clonable(self, planned, cloning_filesystem):
        app = planned()
        plan = build_plan(app, clone_probe=default_clone_probe)
        assert plan.cloneable == plan.files > 0, "the real probe never accepted anything"

    def test_a_file_with_no_digest_is_refused(self, planned, cloning_filesystem):
        # A missing digest is not proof of anything, and cloning replaces one file's contents
        # with another's.
        app = planned()
        for dupe in app.results.groups[0].dupes:
            dupe.digest = b""
        plan = build_plan(app, clone_probe=default_clone_probe)
        assert plan.cloneable == 0

    def test_differing_digests_are_refused(self, planned, cloning_filesystem):
        app = planned()
        for dupe in app.results.groups[0].dupes:
            dupe.digest = b"a-different-digest"
        plan = build_plan(app, clone_probe=default_clone_probe)
        assert plan.cloneable == 0

    def test_it_leaves_no_probe_files_behind(self, planned, tmp_path):
        # can_clone works by actually cloning, so a plan must not litter the user's folders.
        # Runs everywhere: a filesystem that refuses to clone must not leave litter either,
        # which is what #202 was about.
        app = planned()
        build_plan(app, clone_probe=default_clone_probe)
        strays = [p.name for p in (tmp_path / "files").iterdir() if "probe" in p.name or "dupeguru-" in p.name]
        assert strays == [], strays
