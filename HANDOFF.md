# Handoff

Written 2026-08-03, moving development from Windows 11 to a MacBook Air.

## The one rule

This repo is a **fork** of [arsenetar/dupeguru](https://github.com/arsenetar/dupeguru),
maintained at [haggyroth/dupeguru](https://github.com/haggyroth/dupeguru). **Never push,
commit, PR, or open an issue upstream.** Only `origin` is configured and it points at the
fork — keep it that way. The fork exists for our own use, not to feed changes back.

Everything that used to route upstream has been removed or repointed: the Transifex sync
workflow, `.tx/config`, the crash-report dialog, the update checker, the installer help URL,
and the package metadata. `build.py:67` still contains an upstream issue URL, deliberately —
it renders links for *historical* upstream ticket numbers in `help/changelog`. Don't add
`#N` references to that file; they would resolve to unrelated upstream tickets.

## Where things stand

| | |
|---|---|
| Branch | `master` (not `main`) |
| Version | 4.6.0, released |
| Releases | v4.4.0, v4.4.1, v4.5.0, v4.6.0 |
| Issues | 23 closed, 3 open |
| Tests | **814 passing, 6 skipped** on macOS as of #50. Windows/Linux counts differ (see below) |
| CI | green on Python 3.10–3.14 (Linux) plus Windows and macOS |

Work is tracked as GitHub issues on the fork. Don't keep a parallel roadmap file — check the
tracker.

## Setup on the MacBook

```bash
git clone https://github.com/haggyroth/dupeguru.git
cd dupeguru
python3 -m venv env && source env/bin/activate
pip install -r requirements.txt
pip install setuptools               # not seeded in 3.12+ venvs; build.py needs it
pip install pytest'>=7,<8' pytest-cov flake8 black
pip install pre-commit && pre-commit install
python build.py --modules            # builds the C extensions
pytest core hscommon
```

Python **3.10+** is required — `core/hash_cache.py` uses PEP 604 unions in signatures that
are evaluated at import time, so 3.8 and 3.9 cannot import the package at all. `macos.md` has
the Qt/Homebrew setup if you need to run the GUI rather than just the CLI and tests.

Two things the old one-line install got wrong, both found setting this machine up on Python
3.14 (Homebrew's current default):

- **Don't `pip install -r requirements-extra.txt` on 3.14.** It pins
  `pyinstaller>=5.6,<6.0`, and no release in that range has a 3.14 build, so the whole
  install aborts — including the pytest/flake8/black that the same file provides. Install
  those four directly, as above. The pin is only needed for packaging, which nothing here
  currently does. Don't "fix" it by widening the pin to 6.x on its own: PyInstaller majors
  change frozen-build behaviour, that's untestable without an actual packaging run, and it
  would land squarely on the unverified-fix trap that issue #10 exists to avoid. Pair it
  with #10 and real packaging work.
- **`python build.py --modules` needs `setuptools` installed explicitly.** It shells out to
  `setup.py build_ext`, and venvs stopped seeding setuptools in 3.12. Without it the build
  fails with a bare `ModuleNotFoundError: No module named 'setuptools'` several lines above
  the traceback that actually names `build.py`, which is easy to misread as a C toolchain
  problem. It isn't.

`requirements.txt` itself, PyQt5 included, installs cleanly on 3.14.

## What changes when you move to macOS

This matters more than it sounds — several tests are platform-gated, so the pass/skip counts
move and a green run looks different.

| Test group | Windows (local) | macOS | Linux CI |
|---|---|---|---|
| 4 POSIX symlink tests | **skip** — no `SeCreateSymbolicLinkPrivilege` | run | run |
| 3 Windows junction tests | run | **skip** | **skip** |
| 1 case-sensitivity test | skip | skip (APFS is case-insensitive) | run |
| 2 exclude union-mode tests | skip | skip | skip |
| **Totals** | 813 / 7 skipped | **814 / 6 skipped** | 815 / 5 skipped |

820 tests collected in total. Only the macOS column has been measured directly (as of #50);
the other two are that number less the tests their platform skips. If a count is off by a
little, check which group changed rather than assuming the suite broke.

Consequences worth knowing:

- **You gain symlink coverage and lose junction coverage.** The junction tests
  (`core/tests/directories_test.py`) only run on Windows. If you touch
  `core/directories.py::is_traversable_dir`, Windows CI is now your only check on the
  junction half.
- Junctions are *not* symlinks to Python — `is_symlink()` is False and
  `is_dir(follow_symlinks=False)` is True for one. That asymmetry is why that function exists.
  Don't "simplify" it to a plain symlink check.
- **Windows CI runners do have symlink privilege**, so the symlink tests run there too. Only
  this particular Windows machine lacked it.
- The `--help` / cp1252 encoding problem (#29) is Windows-only and won't reproduce on macOS.
  `test_cli_source_is_pure_ascii` still guards it — if it fails, put the character back to
  ASCII rather than assuming it's a false alarm, because you can't feel this one locally.
- Git's `LF will be replaced by CRLF` warnings go away. Files were committed with LF.

## Workflow

Branch off `master` → PR → wait for **fully green** CI → merge commit titled
`merge: <branch-name> (#<PR>)` → delete branch. Kyle authorised auto-merging any PR once CI
is fully green without checking in each time.

Commits follow Conventional Commits. `commitlint` is configured but not enforced locally
unless you `pre-commit install`.

## Two traps that cost time here

**`pre-commit run --all-files` silently skips untracked files.** It reads `git ls-files`, so a
newly created file is invisible until `git add`. This produced a false "all six hooks passed"
locally and a red CI run. **Always `git add` before running it.**

**Verify a fix by reverting it.** `git stash push <file>`, run the new test, confirm it
*fails*, then `git stash pop`. This caught several tests that would otherwise have passed
whether or not the bug was present. Every fix in the last stretch of work was verified this
way, and it's worth continuing.

Minor: running throwaway scripts via `python - <<EOF` breaks `ProcessPoolExecutor`, because
the main module becomes `<stdin>` and spawn workers can't re-import it. Write to a real `.py`
file and set `PYTHONPATH` to the repo root.

## Releases

The process is in [CONTRIBUTING.md](CONTRIBUTING.md#cutting-a-release). Two things there are
easy to get wrong and are written down for that reason: the Sphinx docs take their version
from `help/changelog`, not `core.__version__`; and **GitHub release titles must be bare
semver** (`4.5.0`, not `v4.5.0 — description`). Builds at 4.4.0 and earlier parse the release
*name* as semver, so a descriptive title breaks their update check. That constraint lifts once
nobody is running 4.4.0.

## Open issues

- **[#10](https://github.com/haggyroth/dupeguru/issues/10)** — parallel hashing has no
  `freeze_support()`. The only remaining bug, and deliberately unfixed: it manifests *only* in
  a frozen build, so from a source checkout `sys.frozen` is unset and the call is a no-op.
  Writing the fix without a PyInstaller run to test it would mean shipping something
  unverified and calling it done. Pair it with actual packaging work. Note macOS packaging
  differs from Windows, so a fix verified on one doesn't prove the other.
- **[#27](https://github.com/haggyroth/dupeguru/issues/27)** — PyQt6 alongside PyQt5. A real
  project. Relevant on macOS, where Homebrew increasingly prefers PyQt6. Note `qt/` has no
  automated coverage at all (see below), so this one is hand-testing from the start.
- **[#28](https://github.com/haggyroth/dupeguru/issues/28)** — resumable scans. The largest
  item; the hash cache already delivers most of the practical benefit.

**Read the code before the issue text.** #25 and #26 were both written against a state of the
world that had already moved by the time they were picked up — #25 still asserted `--dry-run`
was a no-op long after it was fixed, and half of #26's GUI ask was already implemented. The
tracker is the roadmap, but it is not a description of the present.

## The Qt layer is untested

CI runs `pytest core hscommon`. **`qt/` is never imported by a test**, and `requirements.txt`
excludes PyQt5 on Linux (`sys_platform != 'linux'`), so the Linux matrix could not run one
anyway. Two consequences:

- Anything wired only through `qt/` is verified by hand or not at all. Check it by driving
  the objects offscreen: `QT_QPA_PLATFORM=offscreen`, construct `qt.app.DupeGuru`, and
  exercise the dialog directly. `PreferencesDialog.save()` only mutates the in-memory prefs
  object — `Preferences.save()` syncs QSettings and runs on app quit — so poking at it in a
  throwaway script will not corrupt real settings.
- Options reach the scanner through `if hasattr(scanner, k): setattr(scanner, k, v)` in
  `DupeGuru.start_scanning`. An option name the scanner does not declare is dropped **with no
  error anywhere**, which is exactly how a feature gets wired into the GUI and never fires.
  `test_scanner_declares_the_options_the_front_ends_set` guards that; extend it when you add
  an option.

Also: `build.py` must find `pyrcc5`. It now errors if it cannot, but before #50 it wrote an
empty `qt/dg_rc.py` and reported success, giving a GUI with no icons and no explanation.
Running `./env/bin/python build.py` without activating the venv is what triggers it.

## Context worth carrying

The original goal was to make this safe to point at a production folder. Three bugs made it
unsafe and are now fixed and released in 4.5.0:

- `--dry-run` was parsed and never read, so `--delete --yes --dry-run` deleted files
- the scan walk followed directory symlinks and Windows junctions, so it could recurse without
  bound and collect files outside the selected folders
- delete-and-replace-with-link deleted the file *before* trying to create the link, and
  swallowed the failure — on a default Windows install that meant file gone, no link, reported
  as a success

If you do run this on real data: use **`--plan`** (added in 4.6.0). It re-validates every
candidate with the same predicate the deletion uses, so it reports what would actually be
removed *and* what would be skipped, and the paths it names are exactly the ones a subsequent
`--delete` touches. `--dry-run` still works and is cheaper to read; `--plan` is the one to
trust before pointing this at anything you care about.

`--exclude` is worth using too — but note that adding any exclusion *replaces* the built-in
"skip dot-prefixed folders" fallback, so pass `--exclude-defaults` alongside it or the scan
will start descending into `.git`.

If you turn on `--partial-hash-threshold` for speed, pair it with `--full-verify`. Sampled
matching can genuinely produce false positives; verification re-reads only the files involved
in those matches and discards the ones that do not hold up.
