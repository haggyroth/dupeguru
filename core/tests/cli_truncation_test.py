# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""A truncated scan says so on the command line, and in the JSON (issue #180).

The failure this prevents is a script: scan, read the groups, delete them, report success. If
the scan gave up half way and the output looked identical to a complete one, the script would
delete a partial answer and call the folder clean. So the signal has to be in the machine
output, not only in a human-readable warning -- and it has to be present on *every* scan, so a
consumer reads a field rather than inferring completeness from the absence of a warning.
"""

import json

import pytest

from cli import EXIT_DUPES_FOUND, main
from core import engine


@pytest.fixture
def duplicates(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"same")
    (tmp_path / "b.txt").write_bytes(b"same")
    return tmp_path


@pytest.fixture
def truncate_matching(monkeypatch):
    """Make content matching give up, without exhausting real memory."""

    def explode(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr(engine.itertools, "combinations", explode)


class TestTheJsonSaysSo:
    def test_a_complete_scan_reports_truncated_false(self, duplicates, capsys):
        assert main([str(duplicates)]) == EXIT_DUPES_FOUND
        stats = json.loads(capsys.readouterr().out)["stats"]
        assert stats["truncated"] is False
        assert stats["truncations"] == []

    def test_a_truncated_scan_reports_truncated_true(self, duplicates, capsys, truncate_matching):
        main([str(duplicates)])
        stats = json.loads(capsys.readouterr().out)["stats"]
        assert stats["truncated"] is True

    def test_the_entry_says_which_stage_and_why(self, duplicates, capsys, truncate_matching):
        main([str(duplicates)])
        [entry] = json.loads(capsys.readouterr().out)["stats"]["truncations"]
        assert entry["stage"] == "content matching"
        assert entry["reason"] == "memory"
        assert "kept" in entry

    def test_the_field_is_present_even_on_a_scan_with_no_duplicates(self, tmp_path, capsys):
        # The reason it is unconditional: a consumer should read completeness rather than
        # infer it from a key that only appears when something went wrong.
        (tmp_path / "a.txt").write_bytes(b"unique A")
        (tmp_path / "b.txt").write_bytes(b"unique B")
        main([str(tmp_path)])
        assert "truncated" in json.loads(capsys.readouterr().out)["stats"]


class TestTheWarningIsUnconditional:
    def test_a_truncated_scan_warns_without_verbose(self, duplicates, capsys, truncate_matching):
        # Not behind --verbose: a caller acting on the output needs this whether or not they
        # asked for chatter.
        main([str(duplicates)])
        err = capsys.readouterr().err
        assert "could not be completed" in err
        assert "content matching" in err

    def test_a_complete_scan_says_nothing_about_it(self, duplicates, capsys):
        main([str(duplicates)])
        assert "could not be completed" not in capsys.readouterr().err

    def test_the_warning_goes_to_stderr_so_the_json_stays_parseable(self, duplicates, capsys, truncate_matching):
        # The whole output contract: stdout is JSON. A warning on stdout would break every
        # consumer at exactly the moment the warning mattered.
        main([str(duplicates)])
        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "could not be completed" in captured.err

    def test_the_warning_says_what_to_do(self, duplicates, capsys, truncate_matching):
        main([str(duplicates)])
        assert "fewer folders" in capsys.readouterr().err


class TestNdjson:
    def test_the_stats_record_carries_it_too(self, duplicates, capsys, truncate_matching):
        main([str(duplicates), "--ndjson"])
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        stats = [line for line in lines if line["type"] == "stats"][0]
        assert stats["truncated"] is True
