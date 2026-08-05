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

import plistlib
import sys
from pathlib import Path

import pytest
import yaml

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


class TestStampMacosBundleVersion:
    """PyInstaller writes an Info.plist, but not a correct version.

    Two separate defects, both only visible in a built app: it writes no `CFBundleVersion`
    at all, which made `build_dmg` die with a KeyError *after* the slow part had succeeded;
    and it left `CFBundleShortVersionString` at "0.0.0", so a shipped 4.9.0 build reported
    itself as 0.0.0 in Finder and in the About box. Neither is reachable from any test that
    does not open a real bundle's plist, which is why they survived this long.
    """

    @staticmethod
    def _bundle(tmp_path, initial):
        contents = tmp_path / "dupeguru.app" / "Contents"
        contents.mkdir(parents=True)
        with open(contents / "Info.plist", "wb") as fp:
            plistlib.dump(initial, fp)
        return tmp_path / "dupeguru.app"

    @staticmethod
    def _read(app):
        with open(app / "Contents" / "Info.plist", "rb") as fp:
            return plistlib.load(fp)

    def test_sets_both_version_keys(self, tmp_path):
        app = self._bundle(tmp_path, {"CFBundleName": "dupeguru", "CFBundleShortVersionString": "0.0.0"})
        package.stamp_macos_bundle_version(str(app), "4.9.0")
        plist = self._read(app)
        assert plist["CFBundleShortVersionString"] == "4.9.0"
        assert plist["CFBundleVersion"] == "4.9.0"

    def test_adds_cfbundleversion_when_missing(self, tmp_path):
        """The KeyError case: PyInstaller omits the key entirely."""
        app = self._bundle(tmp_path, {"CFBundleName": "dupeguru"})
        package.stamp_macos_bundle_version(str(app), "4.9.0")
        assert self._read(app)["CFBundleVersion"] == "4.9.0"

    def test_leaves_other_keys_alone(self, tmp_path):
        app = self._bundle(tmp_path, {"CFBundleName": "dupeguru", "CFBundleIdentifier": "com.hardcoded.dupeguru"})
        package.stamp_macos_bundle_version(str(app), "4.9.0")
        assert self._read(app)["CFBundleIdentifier"] == "com.hardcoded.dupeguru"


class TestBuildDmgVersionFallback:
    """`build_dmg` names the volume after the version, and used to require CFBundleVersion."""

    def test_falls_back_to_the_short_version_string(self):
        plist = {"CFBundleShortVersionString": "4.9.0"}
        version = plist.get("CFBundleVersion") or plist.get("CFBundleShortVersionString") or "unknown"
        assert version == "4.9.0"

    def test_never_raises_when_no_version_is_present(self):
        plist = {"CFBundleName": "dupeguru"}
        version = plist.get("CFBundleVersion") or plist.get("CFBundleShortVersionString") or "unknown"
        assert version == "unknown"


class TestPackagingWorkflow:
    """The application-packaging job must actually build the deliverables.

    `package.py` silently produces an app with no translations and no help if `build.py --loc`
    and `--doc` have not run first, and an artifact upload with a glob that matches nothing
    is a warning, not a failure. Both are the same shape of problem as everything else in
    this file: a green run that shipped nothing.
    """

    @staticmethod
    def _job():
        workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "packaging.yml").read_text(encoding="utf-8"))
        return workflow["jobs"]["applications"]

    def test_builds_both_platforms(self):
        assert set(self._job()["strategy"]["matrix"]["os"]) == {"windows-latest", "macos-latest"}

    def test_one_platform_failing_does_not_cancel_the_other(self):
        assert self._job()["strategy"]["fail-fast"] is False

    def test_builds_resources_before_packaging(self):
        """--loc and --doc must precede package.py, or the app ships without them."""
        run_steps = [s.get("run", "") for s in self._job()["steps"]]
        joined = "\n".join(run_steps)
        for flag in ("--modules", "--loc", "--doc"):
            assert f"build.py {flag}" in joined, f"packaging job never runs build.py {flag}"
        resources = max(i for i, r in enumerate(run_steps) if "build.py --doc" in r)
        packaging = min(i for i, r in enumerate(run_steps) if "package.py" in r)
        assert resources < packaging, "package.py runs before the resources it packages are built"

    def test_verifies_an_artifact_exists(self):
        """package.py exits non-zero on failure now, but a tool can exit 0 and write nothing."""
        joined = "\n".join(s.get("run", "") for s in self._job()["steps"])
        assert "no installer or disk image was produced" in joined

    def test_uploads_a_deliverable_for_each_platform(self):
        uploads = [s for s in self._job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        conditions = " ".join(s.get("if", "") for s in uploads)
        assert "Windows" in conditions and "macOS" in conditions
        paths = " ".join(s["with"]["path"] for s in uploads)
        assert ".exe" in paths and ".dmg" in paths

    def test_macos_ships_a_disk_image_not_a_bare_app(self):
        """Artifacts are zipped, and zip drops the executable bit -- a bare .app would not run."""
        joined = "\n".join(s.get("run", "") for s in self._job()["steps"])
        assert "build_dmg" in joined
        paths = " ".join(
            s["with"]["path"]
            for s in self._job()["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        )
        assert ".app" not in paths

    def test_stays_manual(self):
        """Attaching binaries to releases is a deliberate decision, not a side effect."""
        workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "packaging.yml").read_text(encoding="utf-8"))
        # PyYAML parses a bare `on:` key as the boolean True.
        triggers = workflow.get("on", workflow.get(True))
        assert set(triggers) == {"workflow_dispatch"}, f"packaging must stay manual, found {triggers}"
