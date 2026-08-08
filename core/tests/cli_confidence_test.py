# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""`--plan` and saved results report the same confidence tiers as the GUI (issue #124).

The issue asks for this explicitly, and the reason is worth stating: the two front ends
disagreeing about what is confirmed is worse than neither reporting it. A user who triages in
the GUI and then scripts the same cleanup from the command line has to be able to assume that
"corroborated" means the same thing in both places, or the scripted run quietly acts on a
different set of files than the one they reviewed.

The other thing pinned here is what happens to a results file written before any of this
existed. The tier cannot be recovered from it -- the match kind and the reference-folder state
are simply not in the file -- so the only honest answer is the weakest one.
"""

import json

import pytest

from cli import EXIT_DUPES_FOUND, EXIT_OK, main
from core.confidence import Confidence


def write(directory, files):
    for name, content in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


@pytest.fixture
def scan_dir(tmp_path):
    directory = tmp_path / "scan"
    directory.mkdir()
    return directory


class TestPlanReportsTiers:
    def test_a_shared_filename_is_reported_as_corroborated(self, scan_dir, capsys):
        write(scan_dir, {"originals/report.pdf": b"contents", "backup/report.pdf": b"contents"})
        assert main([str(scan_dir), "--plan"]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.CORROBORATED
        assert payload["stats"]["confidence"][Confidence.CORROBORATED] == 1

    def test_differing_filenames_stop_at_content(self, scan_dir, capsys):
        write(scan_dir, {"notes.txt": b"contents", "notes-old.txt": b"contents"})
        assert main([str(scan_dir), "--plan"]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.CONTENT
        assert payload["stats"]["confidence"][Confidence.CORROBORATED] == 0

    def test_a_reference_folder_corroborates(self, tmp_path, capsys):
        # A --ref folder has to be given positionally as well; the flag only sets the state of a
        # folder already being scanned. The filenames differ so that the only thing that can
        # lift this group is the reference folder itself.
        keep, copies = tmp_path / "keep", tmp_path / "copies"
        write(keep, {"one.txt": b"contents"})
        write(copies, {"two.txt": b"contents"})
        argv = [str(keep), str(copies), "--plan", "--ref", str(keep)]
        assert main(argv) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.CORROBORATED
        assert "Reference" in payload["plan"][0]["confidence_reason"]

    def test_every_tier_is_counted_even_at_zero(self, scan_dir, capsys):
        # A tier missing from the stats reads as "none of those" only to someone who already
        # knows the tier exists, which is not who a machine-readable plan is for.
        write(scan_dir, {"a.txt": b"contents", "b.txt": b"contents"})
        assert main([str(scan_dir), "--plan"]) == EXIT_DUPES_FOUND
        stats = json.loads(capsys.readouterr().out)["stats"]["confidence"]
        assert set(stats) == set(Confidence.ORDER)

    def test_no_duplicates_still_reports_the_tiers(self, scan_dir, capsys):
        write(scan_dir, {"a.txt": b"unique A", "b.txt": b"unique B"})
        assert main([str(scan_dir), "--plan"]) == EXIT_OK
        stats = json.loads(capsys.readouterr().out)["stats"]["confidence"]
        assert stats == {tier: 0 for tier in Confidence.ORDER}

    def test_the_reason_is_carried_alongside_the_tier(self, scan_dir, capsys):
        # "Corroborated" alone does not say by what, and the two corroborating signals are
        # different enough that a user reviewing the plan needs to know which one fired.
        write(scan_dir, {"notes.txt": b"contents", "notes-old.txt": b"contents"})
        assert main([str(scan_dir), "--plan"]) == EXIT_DUPES_FOUND
        assert json.loads(capsys.readouterr().out)["plan"][0]["confidence_reason"]

    def test_the_human_summary_names_the_tier_too(self, scan_dir, capsys):
        write(scan_dir, {"originals/report.pdf": b"contents", "backup/report.pdf": b"contents"})
        assert main([str(scan_dir), "--plan"]) == EXIT_DUPES_FOUND
        assert "corroborated" in capsys.readouterr().err.lower()


class TestSavedResults:
    def test_the_tier_survives_a_save_and_reload(self, scan_dir, tmp_path, capsys):
        write(scan_dir, {"originals/report.pdf": b"contents", "backup/report.pdf": b"contents"})
        saved = tmp_path / "results.json"
        assert main([str(scan_dir), "--output", str(saved)]) == EXIT_DUPES_FOUND
        capsys.readouterr()

        assert main(["--from-results", str(saved), "--plan"]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.CORROBORATED

    def test_a_results_file_predating_the_field_reads_as_unconfirmed(self, scan_dir, tmp_path, capsys):
        # The kind and the reference-folder state are not in the file, so the tier cannot be
        # worked out after the fact. Unconfirmed says "not established here", which is true.
        write(scan_dir, {"originals/report.pdf": b"contents", "backup/report.pdf": b"contents"})
        saved = tmp_path / "results.json"
        assert main([str(scan_dir), "--output", str(saved)]) == EXIT_DUPES_FOUND
        capsys.readouterr()

        data = json.loads(saved.read_text())
        for group in data["groups"]:
            del group["confidence"]
            del group["confidence_reason"]
        saved.write_text(json.dumps(data))

        assert main(["--from-results", str(saved), "--plan"]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.UNCONFIRMED

    def test_an_unrecognised_tier_reads_as_unconfirmed(self, scan_dir, tmp_path, capsys):
        # Hand-edited or written by a newer version. Trusting the string would let an unknown
        # word land in the tally, and anything not understood must not be credited.
        write(scan_dir, {"a.txt": b"contents", "b.txt": b"contents"})
        saved = tmp_path / "results.json"
        assert main([str(scan_dir), "--output", str(saved)]) == EXIT_DUPES_FOUND
        capsys.readouterr()

        data = json.loads(saved.read_text())
        for group in data["groups"]:
            group["confidence"] = "totally-certain"
        saved.write_text(json.dumps(data))

        assert main(["--from-results", str(saved), "--plan"]) == EXIT_DUPES_FOUND
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"][0]["confidence"] == Confidence.UNCONFIRMED
        assert payload["stats"]["confidence"][Confidence.UNCONFIRMED] == 1
