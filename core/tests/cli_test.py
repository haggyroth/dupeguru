"""Tests for the dupeGuru command-line interface (cli.py)."""

import argparse
import json
import sys
from pathlib import Path

import pytest
from hscommon.testutil import eq_

import cli
from cli import main, EXIT_OK, EXIT_DUPES_FOUND, EXIT_BAD_ARGS, EXIT_SCAN_ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_files(directory: Path, names_and_contents: dict) -> None:
    """Create files in *directory* with the given content strings."""
    for name, content in names_and_contents.items():
        (directory / name).write_bytes(content if isinstance(content, bytes) else content.encode())


# ---------------------------------------------------------------------------
# Argument parsing / validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    def test_missing_folder_exits_bad_args(self, capsys):
        rc = main([])
        assert rc == EXIT_BAD_ARGS
        assert "FOLDER" in capsys.readouterr().err or "from-results" in capsys.readouterr().err

    def test_nonexistent_folder_exits_bad_args(self, tmp_path, capsys):
        rc = main([str(tmp_path / "does_not_exist")])
        assert rc == EXIT_BAD_ARGS
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_file_instead_of_folder_exits_bad_args(self, tmp_path, capsys):
        f = tmp_path / "file.txt"
        f.write_text("x")
        rc = main([str(f)])
        assert rc == EXIT_BAD_ARGS
        captured = capsys.readouterr()
        assert "not a directory" in captured.err

    def test_nonexistent_ref_folder_exits_bad_args(self, tmp_path, capsys):
        rc = main([str(tmp_path), "--ref", str(tmp_path / "no_such_ref")])
        assert rc == EXIT_BAD_ARGS
        captured = capsys.readouterr()
        assert "reference folder does not exist" in captured.err

    def test_invalid_mode_exits_bad_args(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main([str(tmp_path), "--mode", "bad_mode"])
        assert exc_info.value.code != 0

    def test_invalid_scan_type_exits_bad_args(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main([str(tmp_path), "--scan-type", "not-a-type"])
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Scan outcomes
# ---------------------------------------------------------------------------


class TestScanOutcomes:
    def test_no_duplicates_returns_exit_ok(self, tmp_path):
        """A folder with unique files should exit 0."""
        _write_files(tmp_path, {"a.txt": "unique content A", "b.txt": "unique content B"})
        rc = main([str(tmp_path)])
        assert rc == EXIT_OK

    def test_duplicates_found_returns_exit_dupes_found(self, tmp_path):
        """Identical files produce at least one group → exit 1."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path)])
        assert rc == EXIT_DUPES_FOUND

    def test_empty_folder_returns_exit_ok(self, tmp_path):
        rc = main([str(tmp_path)])
        assert rc == EXIT_OK


# ---------------------------------------------------------------------------
# JSON output structure
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_written_to_stdout(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "groups" in data
        assert "stats" in data

    def test_json_stats_fields(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path)])
        data = json.loads(capsys.readouterr().out)
        stats = data["stats"]
        assert "groups" in stats
        assert "total_duplicates" in stats
        assert "total_duplicate_size_bytes" in stats
        assert "discarded_files" in stats

    def test_json_group_structure(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path)])
        data = json.loads(capsys.readouterr().out)
        group = data["groups"][0]
        assert "reference" in group
        assert "duplicates" in group
        ref = group["reference"]
        assert "path" in ref
        assert "size" in ref
        assert "mtime" in ref
        assert "is_ref_folder" in ref
        dupe = group["duplicates"][0]
        assert "path" in dupe
        assert "match_percentage" in dupe

    def test_json_written_to_output_file(self, tmp_path):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        out_file = tmp_path / "results.json"
        rc = main([str(tmp_path), "--output", str(out_file)])
        assert rc == EXIT_DUPES_FOUND
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["stats"]["groups"] >= 1

    def test_output_file_error_returns_scan_error(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        # Point output to a directory (not writable as a file)
        rc = main([str(tmp_path), "--output", str(tmp_path)])
        assert rc == EXIT_SCAN_ERROR
        assert "error writing output file" in capsys.readouterr().err

    def test_no_duplicates_groups_empty(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": "unique A", "b.txt": "unique B"})
        main([str(tmp_path)])
        data = json.loads(capsys.readouterr().out)
        assert data["groups"] == []
        assert data["stats"]["groups"] == 0
        assert data["stats"]["total_duplicates"] == 0


# ---------------------------------------------------------------------------
# Reference folder
# ---------------------------------------------------------------------------


class TestRefFolder:
    def test_ref_folder_files_not_marked_as_dupes(self, tmp_path):
        """Files in a ref folder appear as reference in groups, never as dupes."""
        ref_dir = tmp_path / "ref"
        scan_dir = tmp_path / "scan"
        ref_dir.mkdir()
        scan_dir.mkdir()
        content = b"identical content"
        (ref_dir / "ref.txt").write_bytes(content)
        (scan_dir / "copy.txt").write_bytes(content)

        rc, stdout = _capture_json(tmp_path, ref_dir, scan_dir)
        assert rc == EXIT_DUPES_FOUND
        for group in stdout["groups"]:
            for dupe in group["duplicates"]:
                assert not dupe["is_ref_folder"], "ref folder file must not appear as a duplicate"


def _capture_json(tmp_path, ref_dir, scan_dir):
    """Run main() with ref and scan dirs; return (exit_code, parsed_json)."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([str(ref_dir), str(scan_dir), "--ref", str(ref_dir)])
    return rc, json.loads(buf.getvalue())


# ---------------------------------------------------------------------------
# Verbose flag
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    def test_verbose_writes_to_stderr(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--verbose"])
        captured = capsys.readouterr()
        assert "Scanning" in captured.err or "duplicate" in captured.err.lower()

    def test_verbose_does_not_pollute_stdout_json(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--verbose"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "groups" in data


# ---------------------------------------------------------------------------
# NDJSON output
# ---------------------------------------------------------------------------


class TestNdjsonOutput:
    def test_ndjson_each_line_is_valid_json(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--ndjson"])
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert len(lines) >= 2  # at least one group + stats
        for line in lines:
            json.loads(line)  # must not raise

    def test_ndjson_group_lines_have_type_group(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--ndjson"])
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        group_lines = [ln for ln in lines if ln.get("type") == "group"]
        assert len(group_lines) >= 1
        assert "reference" in group_lines[0]
        assert "duplicates" in group_lines[0]

    def test_ndjson_last_line_is_stats(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--ndjson"])
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        stats = lines[-1]
        assert stats["type"] == "stats"
        assert "groups" in stats
        assert "total_duplicates" in stats

    def test_ndjson_no_dupes_has_only_stats_line(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": "unique A", "b.txt": "unique B"})
        rc = main([str(tmp_path), "--ndjson"])
        assert rc == EXIT_OK
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert lines == [
            {
                "type": "stats",
                "groups": 0,
                "total_duplicates": 0,
                "total_duplicate_size_bytes": 0,
                "partial_matches": 0,
                "discarded_files": 0,
            }
        ]

    def test_ndjson_written_to_output_file(self, tmp_path):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        out_file = tmp_path / "results.ndjson"
        rc = main([str(tmp_path), "--ndjson", "--output", str(out_file)])
        assert rc == EXIT_DUPES_FOUND
        lines = [json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()]
        assert lines[-1]["type"] == "stats"


# ---------------------------------------------------------------------------
# Machine-readable progress
# ---------------------------------------------------------------------------


class TestProgressJson:
    def test_progress_json_emits_json_to_stderr(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--progress-json"])
        err_lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
        assert len(err_lines) >= 1
        for line in err_lines:
            obj = json.loads(line)
            assert obj["type"] == "progress"
            assert "percent" in obj
            assert "description" in obj

    def test_progress_json_does_not_pollute_stdout(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--progress-json"])
        data = json.loads(capsys.readouterr().out)
        assert "groups" in data

    def test_verbose_and_progress_json_mutually_exclusive(self, tmp_path, capsys):
        rc = main([str(tmp_path), "--verbose", "--progress-json"])
        assert rc == EXIT_BAD_ARGS
        assert "mutually exclusive" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Scanner knobs
# ---------------------------------------------------------------------------


class TestScannerKnobs:
    def test_min_match_accepted(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--min-match", "50"])
        assert rc in (EXIT_OK, EXIT_DUPES_FOUND)

    def test_word_weighting_accepted(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--word-weighting"])
        assert rc in (EXIT_OK, EXIT_DUPES_FOUND)

    def test_match_similar_accepted(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--match-similar"])
        assert rc in (EXIT_OK, EXIT_DUPES_FOUND)

    def test_mix_file_kind_accepted(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.bin": b"same"})
        rc = main([str(tmp_path), "--mix-file-kind"])
        assert rc in (EXIT_OK, EXIT_DUPES_FOUND)

    def test_min_size_filters_small_files(self, tmp_path, capsys):
        # Files are 4 bytes; min-size 1 KB should exclude them → no dupes
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--min-size", "1"])
        assert rc == EXIT_OK

    def test_knobs_wired_to_app_options(self, tmp_path, capsys):
        """Verify scanner knob values actually reach app.options."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        captured_options = {}

        original_run = cli._run_scan

        def _capture_run(app, verbose, progress_json=False):
            captured_options.update(app.options)
            return original_run(app, verbose, progress_json)

        import unittest.mock as mock

        with mock.patch("cli._run_scan", side_effect=_capture_run):
            main(
                [
                    str(tmp_path),
                    "--min-match",
                    "42",
                    "--word-weighting",
                    "--min-size",
                    "5",
                    "--max-size",
                    "100",
                    "--partial-hash-threshold",
                    "200",
                    "--rehash-ignore-mtime",
                ]
            )

        assert captured_options["min_match_percentage"] == 42
        assert captured_options["word_weighting"] is True
        assert captured_options["size_threshold"] == 5 * 1024
        assert captured_options["large_size_threshold"] == 100 * 1024 * 1024
        assert captured_options["big_file_size_threshold"] == 200 * 1024 * 1024
        assert captured_options["rehash_ignore_mtime"] is True


# ---------------------------------------------------------------------------
# Deletion (--delete / --direct-delete)
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_without_yes_returns_bad_args(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--delete"])
        assert rc == EXIT_BAD_ARGS
        assert "--yes" in capsys.readouterr().err

    def test_direct_delete_without_yes_returns_bad_args(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--direct-delete"])
        assert rc == EXIT_BAD_ARGS

    def test_direct_delete_with_yes_removes_dupe(self, tmp_path):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--direct-delete", "--yes"])
        assert rc == EXIT_DUPES_FOUND
        existing = [f for f in tmp_path.iterdir()]
        assert len(existing) == 1  # one kept, one deleted

    def test_no_dupes_with_delete_returns_ok(self, tmp_path):
        _write_files(tmp_path, {"a.txt": "unique A", "b.txt": "unique B"})
        rc = main([str(tmp_path), "--direct-delete", "--yes"])
        assert rc == EXIT_OK
        # No files should have been deleted
        assert len(list(tmp_path.iterdir())) == 2


# ---------------------------------------------------------------------------
# --dry-run must prevent deletion (issue #7)
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_with_direct_delete_and_yes_deletes_nothing(self, tmp_path, capsys):
        """The regression from #7: --dry-run was ignored entirely by the delete path."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--direct-delete", "--yes", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2, "--dry-run must not delete anything"
        err = capsys.readouterr().err
        assert "DRY RUN" in err
        assert "would permanently delete 1 file(s)" in err

    def test_dry_run_with_trash_delete_deletes_nothing(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--delete", "--yes", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2
        assert "would send to trash" in capsys.readouterr().err

    def test_dry_run_does_not_require_yes(self, tmp_path):
        """A dry run is safe, so it should not be gated behind the --yes confirmation."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--delete", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2

    def test_dry_run_still_emits_results_json(self, tmp_path, capsys):
        """Dry run falls through to normal output so pipelines keep working."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--delete", "--yes", "--dry-run"])
        data = json.loads(capsys.readouterr().out)
        assert data["stats"]["groups"] == 1

    def test_dry_run_leaves_marking_state_untouched(self, tmp_path, capsys):
        """_deletion_plan marks and unmarks; it must not leave results marked."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--delete", "--yes", "--dry-run"])
        data = json.loads(capsys.readouterr().out)
        # Results are still serialisable and complete after the plan ran.
        assert len(data["groups"][0]["duplicates"]) == 1


# ---------------------------------------------------------------------------
# Partial-hash matches must not be deleted silently (issue #9)
# ---------------------------------------------------------------------------

# Engine flags a Match partial=True only when bigsize > 0 and the file exceeds it
# (core/engine.py, getmatches_by_contents). --partial-hash-threshold is in MiB, so
# the smallest usable threshold is 1 MiB and the files must be larger than that.
_PARTIAL_THRESHOLD_MIB = 1
_BIG_FILE = b"x" * (1536 * 1024)  # 1.5 MiB, comfortably over the 1 MiB threshold


class TestPartialMatchGate:
    def test_partial_match_blocks_delete_without_optin(self, tmp_path, capsys):
        """The regression from #9: the CLI deleted partial-hash matches with no warning."""
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main(
            [
                str(tmp_path),
                "--partial-hash-threshold",
                str(_PARTIAL_THRESHOLD_MIB),
                "--direct-delete",
                "--yes",
            ]
        )
        assert rc == EXIT_BAD_ARGS
        assert len(list(tmp_path.iterdir())) == 2, "nothing may be deleted when the gate refuses"
        err = capsys.readouterr().err
        assert "partial (sampled)" in err
        assert "--allow-partial-matches" in err

    def test_partial_match_deleted_with_optin(self, tmp_path):
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main(
            [
                str(tmp_path),
                "--partial-hash-threshold",
                str(_PARTIAL_THRESHOLD_MIB),
                "--direct-delete",
                "--yes",
                "--allow-partial-matches",
            ]
        )
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 1

    def test_dry_run_reports_partial_match_count(self, tmp_path, capsys):
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main(
            [
                str(tmp_path),
                "--partial-hash-threshold",
                str(_PARTIAL_THRESHOLD_MIB),
                "--direct-delete",
                "--yes",
                "--dry-run",
            ]
        )
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2
        err = capsys.readouterr().err
        assert "matched on a partial (sampled) hash only" in err

    def test_full_content_match_is_not_gated(self, tmp_path):
        """Without --partial-hash-threshold nothing is a partial match, so no gate fires."""
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--direct-delete", "--yes"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 1


# ---------------------------------------------------------------------------
# Partial matches are recorded in the output (issue #26)
# ---------------------------------------------------------------------------


def _sampled_twin_pair() -> tuple[bytes, bytes]:
    """Two 4 MiB payloads that agree on every sampled region but differ in full content.

    This is a genuine partial-hash false positive, not merely a pair flagged as partial.
    digest_partial covers bytes [0x4000, 0x8000); digest_samples covers 1 MiB chunks at
    25%, 60% and the tail. Byte 0x30000 (192 KiB) falls outside all of them, so the pair
    is indistinguishable to sampled hashing while genuinely differing on full content.
    """
    size = 4 * 1024 * 1024
    a = bytearray(size)
    # Non-uniform content, so agreement on a sample means something.
    for i in range(0, size, 4096):
        a[i] = (i // 4096) % 251
    b = bytearray(a)
    b[0x30000] ^= 0xFF
    return bytes(a), bytes(b)


class TestPartialMatchSerialisation:
    def test_partial_match_recorded_in_json(self, tmp_path, capsys):
        """match_percentage is 100 either way, so partial_match is the only signal."""
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main([str(tmp_path), "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB)])
        assert rc == EXIT_DUPES_FOUND
        data = json.loads(capsys.readouterr().out)
        dupes = data["groups"][0]["duplicates"]
        assert [d["partial_match"] for d in dupes] == [True]
        assert data["stats"]["partial_matches"] == 1

    def test_full_content_match_recorded_as_not_partial(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path)])
        assert rc == EXIT_DUPES_FOUND
        data = json.loads(capsys.readouterr().out)
        assert [d["partial_match"] for d in data["groups"][0]["duplicates"]] == [False]
        assert data["stats"]["partial_matches"] == 0

    def test_partial_match_recorded_in_ndjson_stats(self, tmp_path, capsys):
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main([str(tmp_path), "--ndjson", "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB)])
        assert rc == EXIT_DUPES_FOUND
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        stats = [line for line in lines if line["type"] == "stats"][0]
        assert stats["partial_matches"] == 1


class TestFromResultsPartialGate:
    """Routing a deletion through --from-results must not bypass the partial-match gate."""

    def _save_partial_results(self, scan_dir, out_file):
        _write_files(scan_dir, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main([str(scan_dir), "--output", str(out_file), "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB)])
        assert rc == EXIT_DUPES_FOUND
        return out_file

    def test_saved_partial_match_blocks_delete_without_optin(self, tmp_path, capsys):
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        out = self._save_partial_results(scan_dir, tmp_path / "results.json")
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--direct-delete", "--yes"])
        assert rc == EXIT_BAD_ARGS
        assert len(list(scan_dir.iterdir())) == 2, "nothing may be deleted when the gate refuses"
        assert "--allow-partial-matches" in capsys.readouterr().err

    def test_saved_partial_match_deleted_with_optin(self, tmp_path, capsys):
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        out = self._save_partial_results(scan_dir, tmp_path / "results.json")
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--direct-delete", "--yes", "--allow-partial-matches"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(scan_dir.iterdir())) == 1

    def test_legacy_results_without_flag_warn_but_proceed(self, tmp_path, capsys):
        """A file predating partial_match cannot be checked; say so rather than imply zero."""
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        out = self._save_partial_results(scan_dir, tmp_path / "results.json")
        data = json.loads(out.read_text())
        for group in data["groups"]:
            for dupe in group["duplicates"]:
                del dupe["partial_match"]
        out.write_text(json.dumps(data))
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--direct-delete", "--yes"])
        assert rc == EXIT_DUPES_FOUND
        assert "cannot be detected or refused" in capsys.readouterr().err
        assert len(list(scan_dir.iterdir())) == 1, "a legacy file must warn, not block"


class TestFullVerify:
    def test_full_verify_discards_sampled_false_positive(self, tmp_path, capsys):
        """The payoff: a pair that sampling calls identical but that genuinely differs."""
        payload_a, payload_b = _sampled_twin_pair()
        _write_files(tmp_path, {"twin_a.bin": payload_a, "twin_b.bin": payload_b})

        # Without verification, sampled hashing reports these as a duplicate pair.
        rc = main([str(tmp_path), "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB)])
        assert rc == EXIT_DUPES_FOUND, "fixture is wrong: these must match on sampled hashes"
        data = json.loads(capsys.readouterr().out)
        assert data["stats"]["partial_matches"] == 1

        rc = main([str(tmp_path), "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB), "--full-verify"])
        assert rc == EXIT_OK, "full verification must reject the false positive"
        out, err = capsys.readouterr()
        assert json.loads(out)["stats"]["groups"] == 0
        assert "1 discarded as false positive" in err

    def test_full_verify_keeps_and_confirms_true_duplicates(self, tmp_path, capsys):
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main([str(tmp_path), "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB), "--full-verify"])
        assert rc == EXIT_DUPES_FOUND
        out, err = capsys.readouterr()
        data = json.loads(out)
        assert data["stats"]["groups"] == 1
        # Verified matches are no longer partial, so nothing is left to warn about.
        assert data["stats"]["partial_matches"] == 0
        assert "1 partial match(es) confirmed" in err

    def test_full_verify_removes_need_for_allow_partial_matches(self, tmp_path):
        """Verification makes the match certain, so the deletion gate has nothing to refuse."""
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main(
            [
                str(tmp_path),
                "--partial-hash-threshold",
                str(_PARTIAL_THRESHOLD_MIB),
                "--full-verify",
                "--direct-delete",
                "--yes",
            ]
        )
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 1

    def test_full_verify_is_a_noop_without_partial_matches(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--full-verify"])
        assert rc == EXIT_DUPES_FOUND
        assert json.loads(capsys.readouterr().out)["stats"]["groups"] == 1


# ---------------------------------------------------------------------------
# --from-results
# ---------------------------------------------------------------------------


class TestFromResults:
    def _scan_and_save(self, tmp_path, out_file):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--output", str(out_file)])
        assert rc == EXIT_DUPES_FOUND
        return out_file

    def test_from_results_re_emits_json(self, tmp_path, capsys):
        out = tmp_path / "results.json"
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        self._scan_and_save(scan_dir, out)
        capsys.readouterr()  # flush

        rc = main(["--from-results", str(out)])
        assert rc == EXIT_DUPES_FOUND
        data = json.loads(capsys.readouterr().out)
        assert data["stats"]["groups"] >= 1

    def test_from_results_dry_run_deletes_nothing(self, tmp_path, capsys):
        """#7 applies to the --from-results deletion path too, not just the scan path."""
        out = tmp_path / "results.json"
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        self._scan_and_save(scan_dir, out)
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--direct-delete", "--yes", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(scan_dir.iterdir())) == 2, "--dry-run must not delete anything"
        err = capsys.readouterr().err
        assert "DRY RUN" in err
        # These results were written by this version, so the partial-match flag is present
        # and the "predates partial-match recording" caveat must not appear.
        assert "predate partial-match recording" not in err

    def test_from_results_ndjson(self, tmp_path, capsys):
        out = tmp_path / "results.json"
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        self._scan_and_save(scan_dir, out)
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--ndjson"])
        assert rc == EXIT_DUPES_FOUND
        lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert lines[-1]["type"] == "stats"

    def test_from_results_with_folders_returns_bad_args(self, tmp_path, capsys):
        out = tmp_path / "results.json"
        out.write_text("{}", encoding="utf-8")
        rc = main([str(tmp_path), "--from-results", str(out)])
        assert rc == EXIT_BAD_ARGS

    def test_from_results_missing_file_returns_bad_args(self, tmp_path, capsys):
        rc = main(["--from-results", str(tmp_path / "no_such.json")])
        assert rc == EXIT_BAD_ARGS

    def test_from_results_delete_requires_yes(self, tmp_path, capsys):
        out = tmp_path / "results.json"
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        self._scan_and_save(scan_dir, out)
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--delete"])
        assert rc == EXIT_BAD_ARGS
        assert "--yes" in capsys.readouterr().err

    def test_from_results_delete_with_yes_removes_file(self, tmp_path):
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        out = tmp_path / "results.json"
        _write_files(scan_dir, {"a.txt": b"same", "b.txt": b"same"})
        main([str(scan_dir), "--output", str(out)])

        rc = main(["--from-results", str(out), "--direct-delete", "--yes"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(scan_dir.iterdir())) == 1

    def test_from_results_ndjson_input(self, tmp_path, capsys):
        """NDJSON saved output can be read back with --from-results."""
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        _write_files(scan_dir, {"a.txt": b"same", "b.txt": b"same"})
        out = tmp_path / "results.ndjson"
        main([str(scan_dir), "--ndjson", "--output", str(out)])
        capsys.readouterr()

        rc = main(["--from-results", str(out)])
        assert rc == EXIT_DUPES_FOUND


# ---------------------------------------------------------------------------
# Headless view shim
# ---------------------------------------------------------------------------


class TestHeadlessView:
    def test_show_message_prints_to_stderr(self, capsys):
        v = cli._HeadlessView()
        v.show_message("hello stderr")
        captured = capsys.readouterr()
        assert "hello stderr" in captured.err

    def test_get_default_returns_fallback(self):
        v = cli._HeadlessView()
        assert v.get_default("missing_key", "fallback") == "fallback"
        assert v.get_default("missing_key") is None

    def test_ask_yes_no_fails_closed(self, capsys):
        """A confirmation nobody can answer must be a "no".

        Auto-confirming would silently accept any safety prompt core adds later
        (partial-hash warnings, cross-device scan warnings) without the user ever
        seeing it. Deliberate confirmation goes through explicit flags instead.
        """
        v = cli._HeadlessView()
        assert v.ask_yes_no("are you sure?") is False
        assert "declined" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Help output must survive a legacy console code page (issue #29)
# ---------------------------------------------------------------------------


class TestHelpEncoding:
    def test_help_text_is_pure_ascii(self):
        """A single non-ASCII character crashed --help on a cp1252 console.

        argparse renders every help string into --help output, and Windows consoles
        default to the code page rather than UTF-8, so U+2192 in the --scan-type help
        raised UnicodeEncodeError before argparse could print anything.
        """
        help_text = cli._build_parser().format_help()
        offenders = sorted({c for c in help_text if ord(c) > 127})
        assert not offenders, f"non-ASCII in --help will crash a cp1252 console: {offenders!r}"

    def test_help_encodes_to_cp1252(self):
        """The direct form of the same guard, against the code page that actually broke."""
        cli._build_parser().format_help().encode("cp1252")

    def test_cli_source_is_pure_ascii(self):
        """Comments too: they get copied into help strings, which is how this happened."""
        from pathlib import Path

        source = Path(cli.__file__).read_text(encoding="utf-8")
        offenders = sorted({c for c in source if ord(c) > 127})
        assert not offenders, f"non-ASCII in cli.py: {offenders!r}"

    def test_prog_name_matches_the_installed_command(self):
        """It read "dupeguru scan", implying a subcommand that does not exist."""
        eq_(cli._build_parser().prog, "dupeguru-scan")

    def test_there_is_no_scan_subcommand(self):
        """The docstring used to document one; folders are positional."""
        parser = cli._build_parser()
        assert not any(
            isinstance(action, argparse._SubParsersAction) for action in parser._actions
        ), "docs and prog name assume no subcommand"

    def test_make_streams_utf8_tolerates_streams_without_reconfigure(self, monkeypatch):
        """pytest's capture replaces sys.stdout with an object that may lack reconfigure."""

        class _Plain:
            pass

        monkeypatch.setattr(sys, "stdout", _Plain())
        monkeypatch.setattr(sys, "stderr", _Plain())
        cli._make_streams_utf8()  # must not raise


# ---------------------------------------------------------------------------
# Documented invocations must actually work (issue #30)
# ---------------------------------------------------------------------------


class TestInvocation:
    def test_python_m_dupeguru_runs(self, tmp_path):
        """`python -m dupeguru` raised ModuleNotFoundError: the checkout dir was not on sys.path."""
        import subprocess

        repo_root = Path(cli.__file__).parent
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})

        result = subprocess.run(
            [sys.executable, "-m", repo_root.name, str(tmp_path)],
            cwd=str(repo_root.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert result.returncode == EXIT_DUPES_FOUND, result.stderr
        data = json.loads(result.stdout)
        eq_(data["stats"]["groups"], 1)

    def test_main_module_does_not_run_on_import(self):
        """__main__.py called sys.exit(main()) unguarded.

        Spawn-based pool workers re-import the main module, so an unguarded call would
        make every worker re-run the whole CLI.
        """
        from pathlib import Path as _Path

        source = _Path(cli.__file__).parent.joinpath("__main__.py").read_text(encoding="utf-8")
        assert 'if __name__ == "__main__":' in source
        body = source.split('if __name__ == "__main__":', 1)[0]
        assert "sys.exit(main())" not in body, "main() must not be invoked at import time"


# ---------------------------------------------------------------------------
# CLI defaults must agree with the GUI, and flag names must describe reality (issue #21)
# ---------------------------------------------------------------------------


class TestScannerFlagSemantics:
    def test_hardlink_filtering_defaults_match_the_gui(self, tmp_path):
        """Same folders, same results, whichever front end you use.

        The CLI defaulted --filter-hardlinks on while the GUI defaults
        ignore_hardlink_matches off, so a CLI scan silently dropped hardlinked pairs a
        GUI scan would report. Compared against the live core default rather than a
        copy of it, so the two cannot drift apart again.
        """
        from core.tests.base import TestApp

        gui_default = TestApp().app.options["ignore_hardlink_matches"]
        args = cli._build_parser().parse_args([str(tmp_path)])
        eq_(args.filter_hardlinks, gui_default)
        eq_(gui_default, False)

    def test_filter_hardlinks_flag_still_opts_in(self, tmp_path):
        args = cli._build_parser().parse_args([str(tmp_path), "--filter-hardlinks"])
        eq_(args.filter_hardlinks, True)

    def test_trust_cache_flag_has_an_accurate_name(self, tmp_path):
        """The old --rehash-ignore-mtime described the opposite of its effect.

        It sets FilesDB.ignore_mtime, which drops mtime from the cache lookup and so
        makes hits *more* likely -- fewer rehashes, not more.
        """
        args = cli._build_parser().parse_args([str(tmp_path), "--trust-cache-ignore-mtime"])
        assert args.trust_cache_ignore_mtime is True

    def test_old_rehash_spelling_still_works(self, tmp_path):
        """Existing scripts must not break on the rename."""
        args = cli._build_parser().parse_args([str(tmp_path), "--rehash-ignore-mtime"])
        assert args.trust_cache_ignore_mtime is True

    def test_trust_cache_flag_defaults_off(self, tmp_path):
        args = cli._build_parser().parse_args([str(tmp_path)])
        eq_(args.trust_cache_ignore_mtime, False)

    def test_trust_cache_flag_reaches_the_core_option(self, tmp_path, monkeypatch):
        captured = {}

        def _capture(app, verbose, progress_json=False):
            captured["ignore_mtime"] = app.options["rehash_ignore_mtime"]
            app.results.groups = []

        monkeypatch.setattr(cli, "_run_scan", _capture)
        main([str(tmp_path), "--trust-cache-ignore-mtime"])
        eq_(captured["ignore_mtime"], True)


# ---------------------------------------------------------------------------
# Exclusions and ignore list (issue #24)
# ---------------------------------------------------------------------------


def _paths_in(data) -> set:
    out = set()
    for group in data["groups"]:
        out.add(group["reference"]["path"])
        for dupe in group["duplicates"]:
            out.add(dupe["path"])
    return out


def _tree_with_junk(root: Path) -> None:
    """keep/ has two identical files; node_modules/ and .hidden/ each hold a third copy."""
    for sub in ("keep", "node_modules", ".hidden"):
        (root / sub).mkdir()
    _write_files(root / "keep", {"a.txt": b"same", "b.txt": b"same"})
    _write_files(root / "node_modules", {"c.txt": b"same"})
    _write_files(root / ".hidden", {"d.txt": b"same"})


class TestExclusions:
    def test_without_exclusions_junk_folders_are_scanned(self, tmp_path, capsys):
        """The gap #24 describes: a scripted run walks node_modules and everything else."""
        _tree_with_junk(tmp_path)
        main([str(tmp_path)])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert any("node_modules" in p for p in paths)

    def test_exclude_keeps_a_folder_out(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        main([str(tmp_path), "--exclude", "^node_modules$"])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any("node_modules" in p for p in paths)
        assert any("keep" in p for p in paths)

    def test_exclude_alone_disables_the_dotfile_fallback(self, tmp_path, capsys):
        """Surprising but real, and the reason --exclude's help says so.

        Directories._default_state_for_path only falls back to "skip names starting with
        a dot" while the exclude list is empty. Marking any pattern replaces that branch
        outright, so adding one exclusion *widens* the scan.
        """
        _tree_with_junk(tmp_path)

        main([str(tmp_path)])
        without = _paths_in(json.loads(capsys.readouterr().out))

        main([str(tmp_path), "--exclude", "^node_modules$"])
        with_exclusion = _paths_in(json.loads(capsys.readouterr().out))

        assert not any(".hidden" in p for p in without)
        assert any(".hidden" in p for p in with_exclusion)

    def test_exclude_defaults_restores_hidden_folder_skipping(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        main([str(tmp_path), "--exclude", "^node_modules$", "--exclude-defaults"])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any(".hidden" in p for p in paths)
        assert not any("node_modules" in p for p in paths)

    def test_exclude_defaults_alone_skips_hidden_folders(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        main([str(tmp_path), "--exclude-defaults"])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any(".hidden" in p for p in paths)

    def test_exclude_can_be_repeated(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        main([str(tmp_path), "--exclude", "^node_modules$", "--exclude", r"^\.hidden$"])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any("node_modules" in p for p in paths)
        assert not any(".hidden" in p for p in paths)

    def test_exclude_from_file(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        listfile = tmp_path / "excludes.txt"
        listfile.write_text("# junk directories\n\n^node_modules$\n\n  ^\\.hidden$  \n", encoding="utf-8")
        main([str(tmp_path), "--exclude-from", str(listfile)])
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any("node_modules" in p for p in paths)
        assert not any(".hidden" in p for p in paths)

    def test_exclude_from_missing_file_is_a_bad_arg(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        rc = main([str(tmp_path), "--exclude-from", str(tmp_path / "nope.txt")])
        eq_(rc, EXIT_BAD_ARGS)
        assert "error reading exclude file" in capsys.readouterr().err

    def test_invalid_regex_is_a_bad_arg(self, tmp_path, capsys):
        _tree_with_junk(tmp_path)
        rc = main([str(tmp_path), "--exclude", "*unclosed["])
        eq_(rc, EXIT_BAD_ARGS)
        assert "cannot use exclusion" in capsys.readouterr().err

    def test_forbidden_overbroad_regex_is_rejected(self, tmp_path, capsys):
        """core.exclude refuses patterns like .* that would exclude everything."""
        _tree_with_junk(tmp_path)
        rc = main([str(tmp_path), "--exclude", ".*"])
        eq_(rc, EXIT_BAD_ARGS)
        assert "cannot use exclusion" in capsys.readouterr().err

    def test_duplicate_exclusion_is_not_an_error(self, tmp_path, capsys):
        """Adding the same pattern twice must still leave it marked, not raise."""
        _tree_with_junk(tmp_path)
        rc = main([str(tmp_path), "--exclude", "^node_modules$", "--exclude", "^node_modules$"])
        assert rc in (EXIT_OK, EXIT_DUPES_FOUND)
        paths = _paths_in(json.loads(capsys.readouterr().out))
        assert not any("node_modules" in p for p in paths)


class TestIgnoreList:
    def _write_ignore_list(self, tmp_path, first, second):
        from core.ignore import IgnoreList

        ignore = IgnoreList()
        ignore.ignore(str(first), str(second))
        dest = tmp_path / "ignore_list.xml"
        ignore.save_to_xml(str(dest))
        return dest

    def test_ignore_list_suppresses_a_recorded_pair(self, tmp_path, capsys):
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same"})

        main([str(scan)])
        eq_(len(json.loads(capsys.readouterr().out)["groups"]), 1)

        listfile = self._write_ignore_list(tmp_path, scan / "a.txt", scan / "b.txt")
        main([str(scan), "--ignore-list", str(listfile)])
        eq_(len(json.loads(capsys.readouterr().out)["groups"]), 0)

    def test_ignore_list_missing_file_is_a_bad_arg(self, tmp_path, capsys):
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(scan), "--ignore-list", str(tmp_path / "nope.xml")])
        eq_(rc, EXIT_BAD_ARGS)
        assert "error reading ignore list" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --plan (issue #25)
# ---------------------------------------------------------------------------


class TestPlanMode:
    def test_plan_needs_no_delete_and_removes_nothing(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--plan"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2, "--plan must never delete"
        out, err = capsys.readouterr()
        assert "DELETION PLAN" in err
        assert "nothing has been deleted" in err
        payload = json.loads(out)
        assert payload["stats"]["would_delete"] == 1
        assert payload["stats"]["groups"] == 1

    def test_plan_emits_verdict_and_confidence_per_file(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--plan"])
        assert rc == EXIT_DUPES_FOUND
        entry = json.loads(capsys.readouterr().out)["plan"][0]["duplicates"][0]
        assert entry["would_delete"] is True
        assert entry["match_confidence"] == "full"
        assert "blocked_reason" not in entry

    def test_plan_marks_partial_matches_as_such(self, tmp_path, capsys):
        _write_files(tmp_path, {"big_a.bin": _BIG_FILE, "big_b.bin": _BIG_FILE})
        rc = main([str(tmp_path), "--plan", "--partial-hash-threshold", str(_PARTIAL_THRESHOLD_MIB)])
        assert rc == EXIT_DUPES_FOUND
        out, err = capsys.readouterr()
        payload = json.loads(out)
        assert payload["plan"][0]["duplicates"][0]["match_confidence"] == "partial"
        assert payload["stats"]["partial_matches"] == 1
        assert payload["stats"]["full_content_matches"] == 0
        assert "refused without --allow-partial-matches" in err

    def test_plan_writes_to_output_file(self, tmp_path, capsys):
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same"})
        out_file = tmp_path / "plan.json"
        rc = main([str(scan), "--plan", "--output", str(out_file)])
        assert rc == EXIT_DUPES_FOUND
        assert capsys.readouterr().out == "", "the plan went to --output, not stdout"
        assert json.loads(out_file.read_text())["stats"]["would_delete"] == 1

    def test_plan_reports_no_duplicates_cleanly(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"unique A", "b.txt": b"unique B"})
        rc = main([str(tmp_path), "--plan"])
        assert rc == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["plan"] == []
        assert payload["stats"]["would_delete"] == 0


class TestPlanRevalidation:
    """The plan must re-check each file, not assume every match is still deletable."""

    def _save(self, scan_dir, out_file):
        _write_files(scan_dir, {"a.txt": b"same", "b.txt": b"same", "c.txt": b"dup", "d.txt": b"dup"})
        assert main([str(scan_dir), "--output", str(out_file)]) == EXIT_DUPES_FOUND
        return json.loads(out_file.read_text())

    def test_changed_and_missing_files_are_reported_as_blocked(self, tmp_path, capsys):
        scan = tmp_path / "scan"
        scan.mkdir()
        out = tmp_path / "results.json"
        self._save(scan, out)
        capsys.readouterr()

        # Break both non-reference files underneath the saved results.
        for group in json.loads(out.read_text())["groups"]:
            dupe = Path(group["duplicates"][0]["path"])
            if dupe.name in ("b.txt", "d.txt"):
                dupe.write_bytes(b"totally different content now")
            else:
                dupe.unlink()

        rc = main(["--from-results", str(out), "--plan"])
        assert rc == EXIT_OK, "nothing is deletable, so nothing would be reclaimed"
        out_text, err = capsys.readouterr()
        stats = json.loads(out_text)["stats"]
        assert stats["would_delete"] == 0
        assert stats["reclaimed_bytes"] == 0
        assert sum(stats["blocked"].values()) == 2
        assert stats["blocked_bytes"] > 0
        assert "would be skipped" in err
        # The prefix used to be baked into the reason as well, doubling the word.
        assert "skipped: skipped:" not in err

    def test_plan_and_deletion_cannot_disagree(self, tmp_path, capsys):
        """The point of the issue: the plan is computed by the deletion's own predicate."""
        scan = tmp_path / "scan"
        scan.mkdir()
        out = tmp_path / "results.json"
        self._save(scan, out)
        capsys.readouterr()

        # Change exactly one duplicate so the plan must refuse it and keep the other.
        changed = None
        for group in json.loads(out.read_text())["groups"]:
            dupe = Path(group["duplicates"][0]["path"])
            if changed is None:
                dupe.write_bytes(b"changed since the scan")
                changed = dupe
        assert changed is not None

        rc = main(["--from-results", str(out), "--plan"])
        assert rc == EXIT_DUPES_FOUND
        plan = json.loads(capsys.readouterr().out)
        predicted_delete = {e["path"] for g in plan["plan"] for e in g["duplicates"] if e["would_delete"]}
        predicted_keep = {e["path"] for g in plan["plan"] for e in g["duplicates"] if not e["would_delete"]}
        assert predicted_keep == {str(changed)}
        assert len(predicted_delete) == 1

        # Now actually delete, and confirm reality matches the prediction exactly.
        rc = main(["--from-results", str(out), "--direct-delete", "--yes"])
        assert rc in (EXIT_DUPES_FOUND, EXIT_SCAN_ERROR)
        for path in predicted_delete:
            assert not Path(path).exists(), f"plan said this would go: {path}"
        for path in predicted_keep:
            assert Path(path).exists(), f"plan said this would be skipped: {path}"


class TestDryRunSummary:
    def test_dry_run_reports_group_and_confidence_counts(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--delete", "--yes", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert len(list(tmp_path.iterdir())) == 2
        err = capsys.readouterr().err
        assert "1 file(s) in 1 group(s)" in err
        assert "1 matched on full content" in err


# ---------------------------------------------------------------------------
# Malformed --from-results input
# ---------------------------------------------------------------------------


class TestMalformedResultsFiles:
    """Pointing --from-results at the wrong file must produce a message, not a traceback.

    Every one of these previously crashed: JSON that is not an object raised AttributeError
    from `data.get`, an NDJSON group record missing its keys raised KeyError, and a binary
    file raised UnicodeDecodeError. The caller only caught OSError and JSONDecodeError.

    It catches ValueError now, which covers JSONDecodeError and UnicodeDecodeError (both
    subclasses) as well as the structural checks in the loader.
    """

    def _run(self, tmp_path, name, content, capsys):
        path = tmp_path / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        rc = main(["--from-results", str(path)])
        return rc, capsys.readouterr().err

    @pytest.mark.parametrize(
        "name,content",
        [
            ("list.json", "[]"),
            ("number.json", "42"),
            ("string.json", '"hello"'),
            ("null.json", "null"),
            ("bool.json", "true"),
        ],
    )
    def test_json_that_is_not_an_object(self, tmp_path, capsys, name, content):
        rc, err = self._run(tmp_path, name, content, capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "error reading results file" in err
        assert "Traceback" not in err

    def test_binary_file(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "bin.dat", b"\x00\x01\x02\xff\xfe", capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "error reading results file" in err
        assert "Traceback" not in err

    def test_groups_key_is_not_a_list(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "g.json", '{"groups": {"not": "a list"}}', capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "must be a list" in err

    def test_ndjson_group_missing_keys_names_the_line(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "a.ndjson", '{"type":"group"}\n{"type":"stats","groups":0}\n', capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "line 1" in err, err
        assert "reference" in err

    def test_ndjson_non_object_line(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "b.ndjson", '{"type":"group","reference":{},"duplicates":[]}\n[1,2]\n', capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "line 2" in err, err

    def test_unparseable_line_names_the_line(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "c.ndjson", '{"type":"group","reference":{},"duplicates":[]}\nnot json\n', capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "line 2" in err, err

    def test_missing_file(self, tmp_path, capsys):
        rc = main(["--from-results", str(tmp_path / "nope.json")])
        eq_(rc, EXIT_BAD_ARGS)
        assert "error reading results file" in capsys.readouterr().err

    def test_valid_file_still_loads(self, tmp_path, capsys):
        """The guard rails must not reject legitimate output."""
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same"})
        out = tmp_path / "r.json"
        eq_(main([str(scan), "--output", str(out)]), EXIT_DUPES_FOUND)
        capsys.readouterr()
        eq_(main(["--from-results", str(out)]), EXIT_DUPES_FOUND)

    def test_valid_ndjson_still_loads(self, tmp_path, capsys):
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same"})
        out = tmp_path / "r.ndjson"
        eq_(main([str(scan), "--ndjson", "--output", str(out)]), EXIT_DUPES_FOUND)
        capsys.readouterr()
        eq_(main(["--from-results", str(out)]), EXIT_DUPES_FOUND)

    def test_empty_file_is_rejected_cleanly(self, tmp_path, capsys):
        rc, err = self._run(tmp_path, "empty.json", "", capsys)
        eq_(rc, EXIT_BAD_ARGS)
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# Picture mode
# ---------------------------------------------------------------------------


def _bmp(width: int = 64, height: int = 64, colour: tuple = (0x33, 0x66, 0xFF)) -> bytes:
    """A minimal 24-bit BMP.

    Hand-built rather than generated with QImage so the fixture itself does not depend on a
    Qt binding -- only the code under test should need one. "bmp" is in Photo.HANDLED_EXTS.
    """
    row_padding = (4 - (width * 3) % 4) % 4
    pixels = bytearray()
    for _ in range(height):
        pixels += bytes(colour[::-1]) * width  # BMP stores BGR
        pixels += b"\x00" * row_padding
    header_size = 54
    return b"".join(
        [
            b"BM",
            (header_size + len(pixels)).to_bytes(4, "little"),
            b"\x00" * 4,
            header_size.to_bytes(4, "little"),
            (40).to_bytes(4, "little"),
            width.to_bytes(4, "little", signed=True),
            height.to_bytes(4, "little", signed=True),
            (1).to_bytes(2, "little"),
            (24).to_bytes(2, "little"),
            b"\x00" * 4,
            len(pixels).to_bytes(4, "little"),
            (2835).to_bytes(4, "little"),
            (2835).to_bytes(4, "little"),
            b"\x00" * 8,
        ]
    ) + bytes(pixels)


@pytest.fixture
def restore_photo_class():
    """PLAT_SPECIFIC_PHOTO_CLASS is a module global; wiring it must not leak between tests."""
    import core.pe.photo

    original = core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS
    yield
    core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = original


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    """Keep the picture cache and hash cache out of the developer's real appdata."""
    from hscommon import desktop

    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(desktop, "special_folder_path", lambda *a, **k: str(appdata))
    return appdata


class TestPictureMode:
    """Picture mode must work without the Qt application ever being constructed.

    core/pe/photo.py leaves PLAT_SPECIFIC_PHOTO_CLASS as None for the UI layer to fill in,
    and only qt/app.py ever did. Every CLI picture scan therefore died on the first file
    with "AttributeError: 'NoneType' object has no attribute 'can_handle'" -- the mode was
    advertised in --help and had never once run.
    """

    def test_picture_mode_finds_identical_images(self, tmp_path, restore_photo_class, isolated_appdata):
        """The regression test: this raised AttributeError before the wiring existed."""
        pytest.importorskip("qtpy", reason="picture mode decodes through a Qt binding")
        (tmp_path / "a.bmp").write_bytes(_bmp())
        (tmp_path / "b.bmp").write_bytes(_bmp())
        rc = main([str(tmp_path), "--mode", "picture", "--dry-run"])
        assert rc == EXIT_DUPES_FOUND

    def test_picture_mode_does_not_match_unrelated_images(self, tmp_path, restore_photo_class, isolated_appdata):
        """Guards the opposite failure: wiring that reports everything as a duplicate."""
        pytest.importorskip("qtpy", reason="picture mode decodes through a Qt binding")
        (tmp_path / "a.bmp").write_bytes(_bmp(colour=(0x00, 0x00, 0x00)))
        (tmp_path / "b.bmp").write_bytes(_bmp(colour=(0xFF, 0xFF, 0xFF)))
        rc = main([str(tmp_path), "--mode", "picture", "--dry-run"])
        assert rc == EXIT_OK

    def test_wiring_is_idempotent(self, restore_photo_class):
        """Called once per scan; it must not clobber a class the GUI already installed."""
        import core.pe.photo

        sentinel = object()
        core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = sentinel
        cli._wire_photo_class()
        assert core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS is sentinel

    def test_missing_qt_binding_reports_one_clear_line(self, monkeypatch, restore_photo_class):
        """Without a binding the user should get a sentence, not an AttributeError."""
        import core.pe.photo

        core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = None
        # Binding a module name to None in sys.modules makes importing it raise ImportError.
        monkeypatch.setitem(sys.modules, "qt.pe.photo", None)
        with pytest.raises(SystemExit) as exc:
            cli._wire_photo_class()
        assert "Picture mode needs a Qt binding" in str(exc.value)

    def test_standard_mode_does_not_import_qt(self, tmp_path, restore_photo_class, isolated_appdata):
        """The Qt import is deferred; a standard scan must not pay for it."""
        import core.pe.photo

        core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS = None
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        rc = main([str(tmp_path), "--dry-run"])
        assert rc == EXIT_DUPES_FOUND
        assert core.pe.photo.PLAT_SPECIFIC_PHOTO_CLASS is None

    def test_match_scaled_flag_reaches_the_options(self):
        args = cli._build_parser().parse_args(["--mode", "picture", "--match-scaled", "/tmp"])
        assert args.match_scaled is True

    def test_match_scaled_defaults_off_to_agree_with_the_gui(self):
        """The CLI's stated convention is that defaults match the GUI's, and the GUI's is off."""
        args = cli._build_parser().parse_args(["--mode", "picture", "/tmp"])
        assert args.match_scaled is False

    def test_resized_duplicates_are_found_only_with_match_scaled(self, tmp_path, restore_photo_class, isolated_appdata):
        """The behavioural test: the flag is what gates cross-dimension matching.

        Without it, matchblock.prepare_pictures buckets by dimension, so a resized copy is
        excluded before scoring -- which is why lowering --min-match never surfaces one.
        The two parser tests above would pass even if the option were dropped on the floor
        between argparse and the scanner; this one would not.
        """
        pytest.importorskip("qtpy", reason="picture mode decodes through a Qt binding")
        (tmp_path / "big.bmp").write_bytes(_bmp(64, 64))
        (tmp_path / "small.bmp").write_bytes(_bmp(32, 32))
        assert main([str(tmp_path), "--mode", "picture", "--dry-run"]) == EXIT_OK
        assert main([str(tmp_path), "--mode", "picture", "--match-scaled", "--dry-run"]) == EXIT_DUPES_FOUND


class TestFileListCache:
    """The --file-list-cache flag (issue #28).

    Correctness first: a cached scan must agree with an uncached one. The speed is only
    worth having if the answer is the same.
    """

    def test_results_match_an_uncached_scan(self, tmp_path):
        db = tmp_path / "fl.db"
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"same", "b.txt": b"same", "c.txt": b"different"})
        uncached = main([str(scan), "--dry-run"])
        first = main([str(scan), "--file-list-cache", str(db), "--dry-run"])
        second = main([str(scan), "--file-list-cache", str(db), "--dry-run"])
        assert uncached == first == second == EXIT_DUPES_FOUND

    def test_a_file_added_after_the_first_scan_is_found(self, tmp_path):
        """Directory mtime moves on add, so the cached listing must be discarded."""
        db = tmp_path / "fl.db"
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"unique one"})
        assert main([str(scan), "--file-list-cache", str(db), "--dry-run"]) == EXIT_OK
        _write_files(scan, {"b.txt": b"unique one"})  # now a duplicate of a.txt
        assert main([str(scan), "--file-list-cache", str(db), "--dry-run"]) == EXIT_DUPES_FOUND

    def test_flag_is_off_by_default(self):
        args = cli._build_parser().parse_args(["/tmp"])
        assert args.file_list_cache is None

    def test_missing_cache_file_is_created_rather_than_failing(self, tmp_path):
        db = tmp_path / "sub" / "fl.db"
        db.parent.mkdir()
        scan = tmp_path / "scan"
        scan.mkdir()
        _write_files(scan, {"a.txt": b"x"})
        assert main([str(scan), "--file-list-cache", str(db), "--dry-run"]) == EXIT_OK
        assert db.exists()
