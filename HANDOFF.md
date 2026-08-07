# Handoff

Written 2026-08-03 moving development from Windows 11 to a MacBook Air; refreshed 2026-08-07 after 4.13.0.

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
| Version | 4.13.0, released, with Windows and macOS binaries attached |
| Releases | v4.4.0 through v4.13.0. From 4.9.0 they carry binaries |
| Issues | 37 closed, 7 open. Two are claimed by open contributor PRs |
| Tests | **1477 passing, 6 skipped** on macOS. Windows/Linux counts differ (see below) |
| Qt bindings | PyQt6 by default, PyQt5 as a fallback with its own CI leg |
| CI | Linux on 3.10 / 3.12 / 3.14, plus Windows, macOS and a PyQt5 leg; `master` is protected |

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

The existing `env/` in this checkout has **both** bindings installed, because the PyQt5
fallback was exercised in it. That changes which one qtpy resolves — see the trap about it
below. A venv built from the commands above has only PyQt6 and resolves correctly.

## What changes when you move to macOS

This matters more than it sounds — several tests are platform-gated, so the pass/skip counts
move and a green run looks different.

| Test group | Windows (local) | macOS | Linux CI |
|---|---|---|---|
| 4 POSIX symlink tests | **skip** — no `SeCreateSymbolicLinkPrivilege` | run | run |
| 3 Windows junction tests | run | **skip** | **skip** |
| 1 case-sensitivity test | skip | skip (APFS is case-insensitive) | run |
| 2 exclude union-mode tests | skip | skip | skip |
| **Totals** | measured on CI | **1477 / 6 skipped** | measured on CI |

Only the macOS column is measured here; the Windows and Linux totals move with the suite and
are best read off a CI run rather than copied into this file, since they went stale within a
week last time. What stays true is *which* groups skip where -- that is the useful part. If a
count is off, check which group changed rather than assuming the suite broke.

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
- **7 required status checks**: `pre-commit` and all six `test (...)` legs. A PR cannot be
  merged while any is pending or failing, which is the point: judging "green" by reading a
  check list is what nearly merged #47 with Windows still running.
- `enforce_admins` is on, so there is no bypass. Zero approvals are required, so
  merge-on-green still needs nobody else.
- Force pushes and branch deletion are off. Tag pushes are unaffected, so releases work.

The matrix deliberately does **not** test every supported Python. It runs the floor (3.10),
the ceiling (3.14) and the version that builds the artifacts (3.12). Version-specific breakage
has only ever appeared at the boundaries — PEP 604 at the floor, setuptools seeding and the
PyInstaller pin at the ceiling — and the intermediate versions never caught anything those did
not. `requirements.txt` still supports the full range, and `tox.ini` still lists every version
for anyone who wants to check locally.

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

## Traps that cost time here

**Qt global state and widget lifetimes cause crashes far from their cause, three times over.**
All three surfaced in a test that had nothing to do with them, and none is visible in review:

* a signal connected straight to *another widget's* bound method keeps that widget's Python
  wrapper alive past the dialog that owns it. Applying preferences re-polishes every widget and
  walks into the corpse. Route such connections through a method on the owner.
* `QApplication.setStyle` is process-wide. A test that switches it and does not put it back
  changes the ground under every later test; one of them segfaulted showing a dialog.
* a `QPixmap` constructed before a `QGuiApplication` exists segfaults. Qt says so plainly, but
  only if you are looking. Any test touching `qt.resources` needs the `qapp` fixture.

`sip.isdeleted()` over `gc.get_objects()` finds the first class directly, and costs 13 ms --
`qt/tests/preferences_dialog_roundtrip_test.py` has a worked example. A weak reference does
*not* work: it dies when the wrapper is collected, which happens either way, while the fault is
a live wrapper around a dead C++ object.

**A Qt widget reference outliving its parent is a Windows-only crash, far from its cause.**
Closing a dialog destroys its children on the C++ side while any Python attributes go on
referencing them. Nothing complains until something walks every widget in the application --
`QApplication.setPalette()` and `setStyle()` both do, and `qt/app.py::_update_options` calls
them every time preferences are applied. On macOS and Linux this is survivable; on Windows it is
an access violation that kills the process. It surfaced in a *preferences* test, twelve tests
into a file that has nothing to do with the dialog at fault. `qt/progress_window.py::close`
clears its children for this reason. If a Windows CI leg dies with `Windows fatal exception:
access violation` inside `_set_style`, look for a widget reference that outlived its window
rather than at the code in the traceback.

**A CI run that is never created is not a CI run that is pending.** During a GitHub Actions
outage the push webhook was dropped, so PR #139 sat at "0 checks" indefinitely -- not queued,
simply never dispatched. Waiting would not have fixed it. Closing and reopening the PR fired a
fresh `pull_request` event and the full matrix ran. Check `gh run list --branch <branch>`: if it
is empty rather than showing something in progress, no amount of patience helps.

**`pre-commit run --all-files` silently skips untracked files.** It reads `git ls-files`, so a
newly created file is invisible until `git add`. This produced a false "all six hooks passed"
locally and a red CI run.

**Use `make check` instead of running the hooks by hand.** It stages first, runs the hooks
twice (the first pass legitimately fails while black and end-of-file-fixer *modify* files, so
only the second result means anything), then runs the suite. That is the whole trap
mechanised; the habit is no longer needed.

**Green does not mean checked.** Four times in one session a green signal meant less than it
looked: a duplicate workflow run showed a passing entry for a check still pending in another
run; an `include` entry converted a CI leg instead of adding one, and the check *count* was
the only clue; a smoke test printed "pool ran" while asserting nothing of the sort; and a
stale PyInstaller analysis produced a build that reported success while shipping the previous
binding. Three of those now have mechanical guards — branch protection,
`tests/ci_workflow_test.py`, and `run_checked` plus `--clean`. The fourth, a test that would
pass whether or not the code under test ran, has no cheap mechanical answer short of mutation
testing; the habit below is the mitigation. Ask what actually ran, not whether it was green.

**Mutation testing is available, and is an audit rather than a gate.** `make mutants` runs
mutmut over the modules scoped in `pyproject.toml`, then summarises. It is not in CI: it is a
periodic check on whether the tests would notice if the code were wrong, not something to put
in front of a merge.

Expect noise, and know its shape before reading the output. On the cache modules it generates
319 mutants; 124 survive, but **83 of those only change the case of a SQL keyword**
(`PRAGMA` → `pragma`), which SQLite treats identically, and another 25 alter an argument to
`logging.debug`. `make mutants-report` splits survivors into `equivalent`, `diagnostic` and
`behaviour`, which turns a 124-line wall into a 16-line shortlist. That shortlist is a
shortlist, not a verdict — some of those are equivalent too, and deciding takes a person.

It earned its keep the first time it ran, finding three real gaps in tests that had themselves
been mutation-verified by hand:

- `if first is None or second is None` in the match cache — changing `or` to `and` survived,
  because the test dropped *both* files. A match naming one missing file would have been
  rebuilt with a `None` side.
- the `partial` flag on restored matches was never asserted, so flipping it survived. That flag
  decides whether deletion warns about sampled hashes.
- `close()` on both caches — inverting `if self.con is not None` survived, because nothing
  checked that closing closed anything.

The lesson worth carrying: verifying a fix by reverting it proves the test catches *that*
mistake. It says nothing about the neighbouring mistakes nobody thought to make.

**Verify a fix by reverting it.** `git stash push <file>`, run the new test, confirm it
*fails*, then `git stash pop`. This caught several tests that would otherwise have passed
whether or not the bug was present. Every fix in the last stretch of work was verified this
way, and it's worth continuing.

**A local test run may be exercising PyQt5, not PyQt6.** qtpy's preference order is
`['pyqt5', 'pyside2', 'pyqt6', 'pyside6']`, so when both bindings are installed it picks
**PyQt5** — the fallback — regardless of PyQt6 being the project default. `requirements.txt`
alone installs only PyQt6, so a clean environment picks PyQt6 correctly; but this checkout's
`env/` has both, because `requirements-pyqt5.txt` was installed into it at some point to
exercise the fallback. Nothing warns you.

That means a green local suite here is testing the *fallback* binding, and the default is
covered only by CI. **The pytest header now names the binding on every Qt run**, so this is
visible rather than something to remember:

```
Qt binding: PyQt5 (override with QT_API=pyqt6)
```
 It bit immediately: `Qt.CheckState.Checked.value` works on PyQt6 and
raises on PyQt5, `int(Qt.CheckState.Checked)` does the reverse, and writing a Qt test locally
gets you the half that passes on whichever binding happens to be resolved. Use
`getattr(x, "value", x)` for enum values, and check which binding you are on before trusting a
Qt result:

```bash
python -c "import qtpy; print(qtpy.API_NAME)"
QT_API=pyqt6 pytest qt          # force the default binding
```

Note that reading `QT_API` from the environment does not tell you anything on its own: qtpy
*sets* it during import to whatever it resolved, so after `import qtpy` it always looks as if
someone configured it deliberately. Read `qtpy.API_NAME` instead.

**Check that a mutation actually applied.** Reverting a fix to confirm the test fails is only
meaningful if the revert landed. A `str.replace` with the wrong indentation silently does
nothing, and the result — tests still passing — is indistinguishable from a surviving mutation.
That happened in #109 and made three sound tests look weak.

**Use `scripts/mutate.py`**, which refuses to do nothing: a target that is missing, or that
matches more times than expected, is an error rather than a silent no-op. It backs the file up
so `restore` cannot put back the wrong thing, and it has its own tests in
`tests/tooling_test.py` — a tool that checks your tests can itself quietly succeed at nothing.

```bash
python scripts/mutate.py apply core/app.py --old "_MTIME_TOLERANCE = 2" --new "_MTIME_TOLERANCE = 999"
pytest core/tests/app_test.py -q          # expect a failure
python scripts/mutate.py restore core/app.py
```

**Measure before optimising, and be willing to throw the plan away.** Three obvious fixes for
the collection bottleneck were measured and rejected: threading gave **1.0x** (16, 64 and 128
workers all matched serial, because the resource is serialized below us), halving the syscalls
per file gave **0.96x** (the second call hits the cache the first populated), and per-file
revalidation on resume — which issue #28 explicitly proposed — would have cost one stat per
file, precisely what the feature exists to avoid. All three were in a written plan before being
measured. None survived.

**A benchmark outside a real app instance is probably lying.** A phase-timing harness reported
hash+match as 98.5% of a *warm* rescan, which would have made the hash cache look useless. It
never connected `filesdb`/`hashcachedb`, which `DupeGuru.__init__` normally does, so every
digest was recomputed from disk. Connecting them moved the warm total from 10.3s to 0.25s.

**Warm caches make measurements meaningless.** The corpus metadata gets cached by the first
walk, so a second measurement of the same tree is measuring RAM, not the disk. Cold and warm
differ by ~3,000x on an external volume. Either measure untouched directories or unmount and
remount the volume between runs.

Minor: running throwaway scripts via `python - <<EOF` breaks `ProcessPoolExecutor`, because
the main module becomes `<stdin>` and spawn workers can't re-import it. Write to a real `.py`
file with an `if __name__ == "__main__"` guard and set `PYTHONPATH` to the repo root.

## Packaging

**Releases carry binaries from 4.9.0 onward**, so packaging bugs are live rather than latent.
That changes the stakes of everything in this section: a packaging bug now reaches whoever
downloads the installer.

The binaries are still built and attached **deliberately**, not as a side effect of tagging —
`.github/workflows/packaging.yml` is `workflow_dispatch` only. The flow used for 4.10.0 through
4.13.0 was:
merge the release PR, tag, dispatch packaging **on the tag**, download the artifacts, verify
them, then upload to the release. Building from the tag matters: for 4.9.0 the first artifacts
were built from a branch three commits ahead and had to be rebuilt, because a binary stamped
4.9.0 containing code 4.9.0 never shipped is a mislabelled release.

Neither release has had the manual pass described below. Nobody has run the NSIS
installer *or* its uninstaller end to end. That is the largest known gap in the release
process.

The workflow has two jobs:

- **`freeze`** — freezes the *CLI* on Windows and macOS and runs a real content scan through
  the frozen binary. This is the only automated way to exercise the process-pool path that
  issue #10 concerned.
- **`applications`** — builds the actual deliverables: the NSIS installer on Windows
  (`dist/dupeGuru_win*_*.exe`) and a disk image on macOS (`dist-dmg/*.dmg`), uploaded as
  artifacts. It runs `build.py --modules --loc --doc` first, because `package.py` will
  happily produce an app with no translations and no help if those have not run, and then
  asserts an artifact of plausible size exists — `package.py` exiting 0 is not proof it
  wrote anything (that was #63). The macOS side uploads a `.dmg` rather than the `.app`:
  GitHub zips artifacts and zip does not carry the executable bit, so a bare bundle would
  download un-runnable.

`tests/packaging_test.py` pins that job's shape — both platforms present, resources built
before packaging, a `.dmg` and not a bare `.app`, and the workflow staying manual — so those
properties have to be changed on purpose.

Two things CI still cannot do, and that therefore need a person: launch the GUI, and run the
NSIS installer/uninstaller end to end.

To build locally on macOS:

```
python build.py --modules && python build.py --loc && python build.py --doc
python package.py
python -c "from hscommon.build import build_dmg; build_dmg('dist/dupeguru.app', 'dist-dmg')"
```

Findings from the first real *application* build on macOS (PyInstaller 6.21) — both were
invisible to every test that does not open a built bundle's `Info.plist`:

- **PyInstaller writes no `CFBundleVersion` at all.** `build_dmg` indexed it directly and died
  with `KeyError` *after* the slow part had already succeeded. It now falls back through
  `CFBundleShortVersionString` to `"unknown"`.
- **`CFBundleShortVersionString` was left at `0.0.0`**, so a 4.9.0 build reported itself as
  0.0.0 in Finder and in the About box. `package.py::stamp_macos_bundle_version` now writes
  both keys from `core.__version__` after PyInstaller runs.

Verified end to end on macOS at 4.9.0: `dupeguru_osx_4_9_0.dmg` (34M) mounts, contains the app
plus the `/Applications` symlink, preserves the executable bit, reports 4.9.0, and the app
launches clean with PyQt6 bundled and `locale/` and `help/` present.

Findings from the earlier *CLI* freeze run, worth knowing before anyone "fixes" #10 again
(it is closed, and was closed on evidence from a real Windows build — 198,000 files scanned
with no extra windows):

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

## The caches, and what each one trusts

Five SQLite caches now, all under `~/Library/Application Support/Hardcoded Software/dupeGuru`
(and the platform equivalents). They differ in what they are willing to believe, which is the
part worth understanding before touching any of them.

| File | Holds | Invalidated by |
|---|---|---|
| `hash_cache.db` (`FilesDB`) | file digests | path + size + mtime |
| `hash_cache2.db` | content hashes for the parallel path | path + size + mtime |
| `cached_pictures.db` (`SqliteCache`) | 15x15 block signatures | per-file mtime |
| `file_list_cache.db` | directory listings | the **directory's** mtime |
| `picture_matches.db` (`MatchCache`) | the match set | every file's identity + all matching params |

The last two arrived in 4.10.0 and are opt-in behind one preference.

**Why the listing cache validates per directory.** Measured on an external exFAT volume: a cold
`lstat` costs 4.5-13.3 ms, a warm one 0.004 ms — about 3,000x. Collecting a 412,589-file corpus
takes 31-92 minutes before a byte is hashed. Validating each cached path individually would
cost one stat per file, which is the entire expense being avoided. One stat per *directory*
instead means 397 stats rather than 412,589 on that corpus.

**The tradeoff that buys.** A directory's mtime moves when an entry is added, removed or
renamed, but **not** when a file is edited in place. Verified on both APFS and exFAT. So a
cache hit can return a stale size for a file whose contents changed. That cannot cause a wrong
deletion — digests come from real content and `check_deletable` re-stats immediately before
removing anything — but it can cause a *missed* duplicate, because files are grouped by size
before hashing. Under-reporting is the safe direction, which is why it is opt-in.

**A directory touched in the last 2 seconds is never cached** (`MTIME_SETTLE_NS`). FAT and
exFAT store 2-second mtimes and NTFS updates directory timestamps lazily, so a change landing
inside one tick would leave the mtime unmoved and the addition invisible. This shipped broken
in #95 with a green CI run and was caught on Windows two PRs later.

**The match cache is deliberately stricter.** Its key covers size and mtime, not just paths, so
an in-place edit invalidates it. In the listing cache a stale entry costs a missed match; here
it would show the user duplicates that no longer exist, and a results table that disagrees with
the disk is the kind of wrong that costs trust. Invalidation is all-or-nothing under a key
covering every matching parameter — per-row invalidation would mean working out which matches a
changed file could have participated in, which is the comparison being avoided.

**`purge_outdated` is scoped.** It used to re-stat every directory the picture cache had ever
seen, so cost grew with usage history rather than with the scan: two 12 KB local files against
a cache holding 20,000 external-drive rows took 331s. Scoped to the directories actually being
scanned it takes 0.23s. It also no longer deletes rows for an unreachable directory — unplugging
a drive used to discard everything cached from it.

## Where the time actually goes

Measured per phase, 15,294 files with 5,723 duplicate groups, both caches warm:

| | standard content mode | picture mode |
|---|---:|---:|
| collect | 0.117s | 0.128s |
| hash / block + match | 0.113s | 15.856s → **0.015s** with the match cache |
| group | 0.023s | 0.023s |

Content mode has nothing left worth caching — that is why checkpoint 2 of #28 was declined
rather than built. Picture matching was 99.1% of a warm rescan and is now the thing the match
cache removes.

## Releases

The process is in [CONTRIBUTING.md](CONTRIBUTING.md#cutting-a-release). Two things there are
easy to get wrong and are written down for that reason: the Sphinx docs take their version
from `help/changelog`, not `core.__version__`; and **GitHub release titles must be bare
semver** (`4.5.0`, not `v4.5.0 — description`). Builds at 4.4.0 and earlier parse the release
*name* as semver, so a descriptive title breaks their update check. That constraint lifts once
nobody is running 4.4.0.

## Open issues

- **[#83](https://github.com/haggyroth/dupeguru/issues/83)** — a CLI `--version` flag. Small,
  and deliberately left alone: an outside contributor opened a PR for it and was given review
  feedback. Taking it over would be faster and would also be the last time they contributed.
  Nothing depends on it.

Everything else open is an unstarted feature proposal, written by me rather than requested by
anyone. Read them sceptically; several say so themselves.

- **#125** (record deletions, offer to restore) and **#134** (warn before scanning a system
  location) are the two labelled `safety`. #125 is the most valuable thing left: cloning (#129)
  made deletion non-destructive only for byte-identical files on a filesystem that supports it,
  so picture-mode matches and cross-volume duplicates are still genuinely destructive, and
  nothing records what went.
- **#122, #123, #124, #127** are the review-workflow cluster — folder-pair rollup, ordering by
  reclaimable space, confidence triage, folder overlap scoring. All are about acting on more
  files with less individual scrutiny, which #131's preview was built to make reasonable. #123
  is the most concrete of them.
- **#128** (find exact duplicates and visually similar images in one scan) is the largest.
- **#130** (prioritise photos by EXIF capture date) is genuinely small.
- **#126** (report only duplicates involving newly added files) is speculative in the way #133
  was: it assumes repeat scanning of the same tree, and nothing has established that anyone
  does. #133 was built anyway on request; that does not make #126 evidence for itself.

Closed, but the reasoning is worth keeping:

- **#28** (resumable scans) was closed on measurement rather than completion. Checkpoints 1
  and 3 shipped as the listing cache and the picture match cache; checkpoint 2 was **declined**
  because the hash cache already delivered it and a warm content rescan measured 0.25s. The
  issue's own proposal -- revalidating every persisted path on resume -- would have cost one
  stat per file, which is precisely what the feature exists to avoid.
- **#82** (coverage) was closed on substance. The four methods that could destroy or misreport
  user data went from 12/18/3/66% to 100/100/100/86%. What remained was plumbing.
- **#10** (`freeze_support()`) and **#27** (PyQt6 alongside PyQt5) both needed a real frozen
  build to settle, which is why they sat open so long. #27 was phased across #52–#56.

## What to do next

No issue is open for any of these; they are judgement calls rather than tracked work.

**1. Run the installer and uninstaller on Windows.** The largest gap. Five releases now carry
binaries and nobody has run the NSIS installer end to end, or its uninstaller — whose
`RMDir /r "$INSTDIR\_internal"` has never been executed. If it is wrong it either strands files
or removes too much. This needs the Windows machine; CI cannot do it.

**2. Decide about signing.** Neither binary is signed or notarized, so macOS Gatekeeper refuses
the app on first launch and Windows SmartScreen warns. The release notes say so, but every
downloader hits it. Apple notarization needs a paid developer account; that is a spending
decision, not a technical one.

**3. Measure the caches cold, on a corpus that is not already warm.** Every speedup quoted in
this file was measured on macOS against one exFAT volume, mostly with warm metadata. The
cold-path numbers are extrapolations from per-file costs, not observations of a full run. An
unmount/remount between passes gives the real figure.

**4. The Qt layer is at 69%, and the risky parts are now covered.** Everything that decides
which files are kept or deleted has tests: the re-prioritize and mark-by-rule dialogs, the
folder state list, and all three preferences dialogs. What is left is lower stakes --
`error_report_dialog.py` at 0% is the one worth doing, because it is what appears when
something breaks, so a fault in it hides real errors. The image viewer is large and mostly
uncovered but a wrong render there costs a look, not a file.

**Testing preferences needs two directions, not one.** A round trip -- load the dialog, save it
back untouched, expect no change -- catches a preference missing from `_load`. It *cannot*
catch one missing from `_save`: a value that is never written keeps whatever was there, so the
cycle looks clean. That version missed three of five seeded faults. The save direction has to
be driven by moving the widgets and checking the values follow.

**5. Watch `merge_similar_words`.** It is genuinely quadratic — 4.0x per doubling, measured. The
test now guards against a return to *cubic*, which is what an O(n) membership check in the loop
produces, but the quadratic cost itself is real and will bite on a large filename scan.

Deliberately not proposed: chasing coverage percentages in `exif.py`, `core/me/fs.py` or the
remaining plumbing in `core/app.py`. Those numbers would improve without the risk improving,
which is the argument #82 was closed on.

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
