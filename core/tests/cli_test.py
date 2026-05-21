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
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

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
