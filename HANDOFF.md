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
| Version | 4.8.0, released |
| Releases | v4.4.0, v4.4.1, v4.5.0, v4.6.0, v4.7.0, v4.7.1, v4.8.0 |
| Issues | 26 closed, 1 open |
| Tests | **887 passing, 6 skipped** on macOS. Windows/Linux counts differ (see below) |
| Qt bindings | PyQt6 by default, PyQt5 as a fallback with its own CI leg |
| CI | green on Python 3.10–3.14 (Linux) plus Windows and macOS; `master` is protected |

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

- ~~Don't `pip install -r requirements-extra.txt` on 3.14.~~ **Fixed in #58.** The pin was
  `pyinstaller>=5.6,<6.0`, which has no 3.14 build, so the whole file failed to install —
  taking pytest/flake8/black with it. It is now `>=6.15,<7.0` and installs cleanly. The pin
  was widened together with actual packaging verification rather than on its own, which is
  what made it safe to do: see the packaging section below.
- **`python build.py --modules` needs `setuptools` installed explicitly.** It shells out to
  `setup.py build_ext`, and venvs stopped seeding setuptools in 3.12. Without it the build
  fails with a bare `ModuleNotFoundError: No module named 'setuptools'` several lines above
  the traceback that actually names `build.py`, which is easy to misread as a C toolchain
  problem. It isn't.

`requirements.txt`, PyQt6 included, installs cleanly on 3.14.

## What changes when you move to macOS

This matters more than it sounds — several tests are platform-gated, so the pass/skip counts
move and a green run looks different.

| Test group | Windows (local) | macOS | Linux CI |
|---|---|---|---|
| 4 POSIX symlink tests | **skip** — no `SeCreateSymbolicLinkPrivilege` | run | run |
| 3 Windows junction tests | run | **skip** | **skip** |
| 1 case-sensitivity test | skip | skip (APFS is case-insensitive) | run |
| 2 exclude union-mode tests | skip | skip | skip |
| **Totals** | 890 / 3 skipped | **887 / 6 skipped** | 888 / 5 skipped |

893 tests collected in total. The macOS column is measured; the other two are that total
less the tests their platform skips, and were last measured on CI at #56. If a count is
off by a little, check which group changed rather than assuming the suite broke.

Note the Windows column is CI, not the old Windows laptop: CI runners *do* have symlink
privilege, so they run both the symlink and the junction tests and skip the least. And since
#56 every platform runs the ~21 Qt tests, so none of the three columns skips them any more.

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

`master` is **protected**, and this is enforced by GitHub rather than by remembering:

- Direct pushes are rejected — `GH006: ... Changes must be made through a pull request`.
- **9 required status checks**: `pre-commit` and all eight `test (...)` legs. A PR cannot be
  merged while any is pending or failing, which is the point: judging "green" by reading a
  check list is what nearly merged #47 with Windows still running.
- `enforce_admins` is on, so there is no bypass. Zero approvals are required, so
  merge-on-green still needs nobody else.
- Force pushes and branch deletion are off. Tag pushes are unaffected, so releases work.

**Adding or renaming a CI leg breaks merging until the required-contexts list is updated**,
because contexts are matched by name. That is deliberate — a leg silently vanishing is the
failure this guards — but it means the two go together:

```bash
gh api repos/haggyroth/dupeguru/branches/master/protection | \
    python -c "import json,sys; print(*json.load(sys.stdin)['required_status_checks']['contexts'], sep='\n')"
```

`tests/ci_workflow_test.py` pins the expected job set, so a matrix change is a visible edit
in the diff rather than something to discover later.

Commits follow Conventional Commits. `commitlint` is configured but not enforced locally
unless you `pre-commit install`.

## Two traps that cost time here

**`pre-commit run --all-files` silently skips untracked files.** It reads `git ls-files`, so a
newly created file is invisible until `git add`. This produced a false "all six hooks passed"
locally and a red CI run. **Always `git add` before running it.**

**Green does not mean checked.** Four times in one session a green signal meant less than it
looked: a duplicate workflow run showed a passing entry for a check still pending in another
run; an `include` entry converted a CI leg instead of adding one, and the check *count* was
the only clue; a smoke test printed "pool ran" while asserting nothing of the sort; and a
stale PyInstaller analysis produced a build that reported success while shipping the previous
binding. Three of those now have mechanical guards — branch protection,
`tests/ci_workflow_test.py`, and `run_checked` plus `--clean`. The fourth, a test that would
pass whether or not the code under test ran, has no cheap mechanical answer short of mutation
testing; the habit below is the mitigation. Ask what actually ran, not whether it was green.

**Verify a fix by reverting it.** `git stash push <file>`, run the new test, confirm it
*fails*, then `git stash pop`. This caught several tests that would otherwise have passed
whether or not the bug was present. Every fix in the last stretch of work was verified this
way, and it's worth continuing.

Minor: running throwaway scripts via `python - <<EOF` breaks `ProcessPoolExecutor`, because
the main module becomes `<stdin>` and spawn workers can't re-import it. Write to a real `.py`
file and set `PYTHONPATH` to the repo root.

## Packaging

Nothing here has ever shipped a binary — every release has zero assets — so packaging bugs
are latent rather than live. `.github/workflows/packaging.yml` is **manual only**
(`workflow_dispatch`): it freezes the CLI on Windows and macOS and runs a real content scan
through the frozen binary, which is the only automated way to exercise the process-pool path
that issue #10 concerns.

Two things it cannot do, and that therefore still need a person: launch the GUI (the symptom
in #10 is extra windows), and run the NSIS installer/uninstaller (#27's `setup.nsi` item).

Findings from the first real packaging run, on macOS with PyInstaller 6.21 — worth knowing
before anyone "fixes" #10 again:

- The **frozen CLI is not affected**. Measured: 11 concurrent worker processes, 300 groups
  found, exit 1, no re-execution markers. `cli.py` imports at module scope, so anything that
  hands control to the worker machinery runs before `main()` does.
- **PyInstaller ships `pyi_rth_multiprocessing.py`**, a runtime hook whose explicit job is to
  stop `spawn` re-reading `__main__` from the main script. That is #10's exact mechanism, and
  it has been in PyInstaller since 2017 — so it is in the 5.x range the project used to pin.
  #10 may therefore be mitigated by the packaging tool already. It is still **unconfirmed on
  Windows with a GUI build**, which is the only configuration the issue actually describes.
- `freeze_support()` now sits in the `__main__` block of all three entry points regardless.
  That is the documented contract, and it does not depend on the packaging tool's behaviour.

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
- **[#27](https://github.com/haggyroth/dupeguru/issues/27)** — PyQt6 alongside PyQt5, with
  PyQt6 as the default and PyQt5 as the fallback. Phased: **phase 0** (Qt smoke tests, #52)
  **phase 1** (#53), **phase 2** (#54), **phase 3a** (#55) and **phase 3b** (#56) are all
  done: PyQt6 is the default binding, PyQt5 is a supported fallback with its own CI leg, and
  nothing in the tree imports a binding directly. What remains of #27 is **packaging** — and
  that half is unverifiable here, since nobody can run a frozen build; see #10.
- **[#28](https://github.com/haggyroth/dupeguru/issues/28)** — resumable scans. The largest
  item; the hash cache already delivers most of the practical benefit.

**Read the code before the issue text.** #25 and #26 were both written against a state of the
world that had already moved by the time they were picked up — #25 still asserted `--dry-run`
was a no-op long after it was fixed, and half of #26's GUI ask was already implemented. The
tracker is the roadmap, but it is not a description of the present.

## The Qt layer has smoke coverage only

Since #52, CI runs `pytest core hscommon qt` and `qt/tests/` exists. It is **smoke coverage**:
the widgets construct, preferences reach the scan options, and resources resolve. Nothing
asserts layout or behaviour, so a change that renders wrongly still passes. Treat a green run
as "it did not explode", not "it works".

- Since #56 the Qt tests run on **every** leg, Linux included — the `sys_platform != 'linux'`
  exclusion that made Linux skip them is gone, because PyQt6 has manylinux wheels where PyQt5
  did not. Qt wheels do not bundle the system libraries Qt links against, so the Linux legs
  install `libegl1`, `libgl1`, `libxkbcommon-x11-0` and `libdbus-1-3`; a missing one shows up
  as the offscreen platform plugin failing to load, not as a Python error.
- One leg runs the **PyQt5 fallback**, with PyQt6 uninstalled rather than overridden by
  `QT_API`, so it exercises what a user on the fallback actually has. If you touch Qt code,
  a green PyQt6 run does not mean the fallback still works — check that leg too.
- **Import Qt through `qtpy`, never a binding directly.** `from qtpy.QtWidgets import ...`.
  PyQt6 is the default; `pip install -r requirements-pyqt5.txt` and `QT_API=pyqt5` switches.
  qtpy also normalises the signal/slot names: use `Signal` and `Slot`, not `pyqtSignal` and
  `pyqtSlot`, which it does not export. Where no binding is installed it raises
  `QtBindingsNotFoundError`, an `ImportError` subclass, which is what lets `importorskip`
  skip the widget tests cleanly on Linux.
- The resource tests need no bindings and no build step, so they *do* run on Linux. They are
  the only Qt-adjacent thing that does.
- To check something by hand, drive the objects offscreen: `QT_QPA_PLATFORM=offscreen`,
  construct `qt.app.DupeGuru`, exercise the dialog. `PreferencesDialog.save()` only mutates
  the in-memory prefs object — `Preferences.save()` syncs QSettings and runs on app quit — so
  a throwaway script will not corrupt real settings. `qt/tests/conftest.py` goes further and
  sandboxes both settings and appdata via `QStandardPaths.setTestModeEnabled`.
- Options reach the scanner through `if hasattr(scanner, k): setattr(scanner, k, v)` in
  `DupeGuru.start_scanning`. An option name the scanner does not declare is dropped **with no
  error anywhere**, which is exactly how a feature gets wired into the GUI and never fires.
  `test_scanner_declares_the_options_the_front_ends_set` and its Qt-side counterpart guard
  that; extend both when you add an option.

## Images are embedded, and committed

Since #54 there is no resource build step at all. `qt/resources_data.py` holds the eleven
images as base64 and is **committed**, generated from `qt/dg.qrc` by
`python build.py --resources`. Load them through `qt.resources.icon(name)` /
`qt.resources.pixmap(name)`; the old `QPixmap(":/name")` scheme is gone, as is `pyrcc5`,
which PyQt6 does not ship.

- **Change an image, re-run `python build.py --resources` and commit the result.**
  `qt/tests/resources_test.py` regenerates and compares, so forgetting fails CI with a
  message telling you the command — but only if you look.
- Embedded rather than read from `images/` on disk because that is what makes frozen builds
  work: `package.py` bundles only two logos, and fixing that properly lands on the packaging
  work nobody here can verify (#10).
- Committed rather than generated during the build because the generated-at-build-time
  version is what failed silently in #50: a missing `pyrcc5` still produced a (empty)
  `dg_rc.py` and a "build succeeded". There is now no step left to fail.
- `qt/dg.qrc` survives as the alias-to-image manifest. It is no longer compiled by anything;
  it is just the input our generator reads.

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
