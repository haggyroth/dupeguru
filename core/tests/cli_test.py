"""Tests for the dupeGuru command-line interface (cli.py)."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

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
        rc = main([str(tmp_path)])
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
        lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
        assert len(lines) >= 2  # at least one group + stats
        for line in lines:
            json.loads(line)  # must not raise

    def test_ndjson_group_lines_have_type_group(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--ndjson"])
        lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
        group_lines = [l for l in lines if l.get("type") == "group"]
        assert len(group_lines) >= 1
        assert "reference" in group_lines[0]
        assert "duplicates" in group_lines[0]

    def test_ndjson_last_line_is_stats(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--ndjson"])
        lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
        stats = lines[-1]
        assert stats["type"] == "stats"
        assert "groups" in stats
        assert "total_duplicates" in stats

    def test_ndjson_no_dupes_has_only_stats_line(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": "unique A", "b.txt": "unique B"})
        rc = main([str(tmp_path), "--ndjson"])
        assert rc == EXIT_OK
        lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
        assert lines == [{"type": "stats", "groups": 0, "total_duplicates": 0,
                          "total_duplicate_size_bytes": 0, "discarded_files": 0}]

    def test_ndjson_written_to_output_file(self, tmp_path):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        out_file = tmp_path / "results.ndjson"
        rc = main([str(tmp_path), "--ndjson", "--output", str(out_file)])
        assert rc == EXIT_DUPES_FOUND
        lines = [json.loads(l) for l in out_file.read_text().splitlines() if l.strip()]
        assert lines[-1]["type"] == "stats"


# ---------------------------------------------------------------------------
# Machine-readable progress
# ---------------------------------------------------------------------------

class TestProgressJson:
    def test_progress_json_emits_json_to_stderr(self, tmp_path, capsys):
        _write_files(tmp_path, {"a.txt": b"same", "b.txt": b"same"})
        main([str(tmp_path), "--progress-json"])
        err_lines = [l for l in capsys.readouterr().err.splitlines() if l.strip()]
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
            main([str(tmp_path), "--min-match", "42", "--word-weighting",
                  "--min-size", "5", "--max-size", "100",
                  "--partial-hash-threshold", "200", "--rehash-ignore-mtime"])

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

    def test_from_results_ndjson(self, tmp_path, capsys):
        out = tmp_path / "results.json"
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        self._scan_and_save(scan_dir, out)
        capsys.readouterr()

        rc = main(["--from-results", str(out), "--ndjson"])
        assert rc == EXIT_DUPES_FOUND
        lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
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

    def test_ask_yes_no_returns_true(self):
        v = cli._HeadlessView()
        assert v.ask_yes_no("are you sure?") is True
