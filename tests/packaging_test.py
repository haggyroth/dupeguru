# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for the repo-level packaging script.

`package.py` builds artefacts and so is mostly unrunnable outside a real packaging
environment, but the parts that decide whether packaging *failed* are ordinary logic and
need no Windows: locating makensis, and turning a failure into a non-zero exit status.

Those are exactly the parts that were wrong. `package_windows` discarded makensis' exit
code and `main()` returned None regardless, so a failed installer step exited 0 while
PyInstaller's output sat in dist/ looking like a successful build (issue #63).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

package = pytest.importorskip("package", reason="packaging deps not installed")


class TestFindMakensis:
    def test_prefers_makensis_on_path(self, monkeypatch):
        monkeypatch.setattr(package.shutil, "which", lambda name: "/somewhere/makensis")
        assert package.find_makensis() == "/somewhere/makensis"

    def test_falls_back_to_program_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(package.shutil, "which", lambda name: None)
        nsis = tmp_path / "NSIS"
        nsis.mkdir()
        (nsis / "makensis.exe").write_text("")
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert package.find_makensis() == str(nsis / "makensis.exe")

    def test_finds_the_older_bin_layout(self, monkeypatch, tmp_path):
        """NSIS 3.x installs makensis.exe directly; older builds used Bin/."""
        monkeypatch.setattr(package.shutil, "which", lambda name: None)
        bindir = tmp_path / "NSIS" / "Bin"
        bindir.mkdir(parents=True)
        (bindir / "makensis.exe").write_text("")
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert package.find_makensis() == str(bindir / "makensis.exe")

    def test_returns_none_when_absent(self, monkeypatch, tmp_path):
        """The case that used to be silent: nothing found, and the build carried on."""
        monkeypatch.setattr(package.shutil, "which", lambda name: None)
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert package.find_makensis() is None

    def test_does_not_hardcode_a_drive_letter(self, monkeypatch, tmp_path):
        """The original bug: one absolute path baked in, so any other install was invisible."""
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "custom root"))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        candidates = package._nsis_fallback_paths()
        assert candidates, "no fallback paths derived from the environment"
        assert all(str(tmp_path) in c for c in candidates), candidates


class TestInstallerPath:
    def test_matches_the_outfile_directive(self):
        """setup.nsi: OutFile "${DISTDIR}\\${APPNAME}_win${BITS}_${MAJOR}.${MINOR}.${PATCH}.exe"."""
        assert package.installer_path("64", ["4", "7", "1"]).replace("\\", "/") == ("dist/dupeGuru_win64_4.7.1.exe")


class TestExitStatusPropagation:
    """`main()` used to return None whatever happened, so the shell always saw success."""

    def _run_main(self, monkeypatch, platform_name, result):
        monkeypatch.setattr(package.sys, "platform", platform_name)
        monkeypatch.setattr(package, "parse_args", lambda: type("A", (), {"src_pkg": False, "arch_pkg": False})())
        target = "package_windows" if platform_name == "win32" else "package_macos"
        monkeypatch.setattr(package, target, lambda: result)
        return package.main()

    @pytest.mark.parametrize("platform_name", ["win32", "darwin"])
    def test_failure_propagates(self, monkeypatch, platform_name):
        assert self._run_main(monkeypatch, platform_name, 1) == 1

    @pytest.mark.parametrize("platform_name", ["win32", "darwin"])
    def test_success_is_zero(self, monkeypatch, platform_name):
        assert self._run_main(monkeypatch, platform_name, 0) == 0

    @pytest.mark.parametrize("platform_name", ["win32", "darwin"])
    def test_none_is_treated_as_success(self, monkeypatch, platform_name):
        """Packagers that do not report status must not turn into spurious failures."""
        assert self._run_main(monkeypatch, platform_name, None) == 0
