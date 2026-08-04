# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- **Packaging reported success when the installer step failed** (`package.py`, issue #63):
  `package_windows` discarded makensis' exit code and `main()` returned `None` regardless, so
  `python package.py` exited 0 whether or not an installer had been produced. PyInstaller has
  already filled `dist/` by that point, so a failed run left a tree that looked like a good
  build minus the one artifact anyone would ship. The makensis path was also hardcoded to a
  single `Program Files` location, with a pre-existing TODO — an NSIS installed anywhere else
  produced a "not recognized" error whose exit code was then thrown away, which made the
  fragile half invisible. `find_makensis()` now prefers PATH and falls back to locations
  derived from the `ProgramFiles` environment variables rather than a baked-in drive letter,
  covering both the modern and pre-3.x NSIS layouts; the exit code is checked; the installer
  file is confirmed to exist afterwards, since a tool can report success and write nothing;
  and `main()` returns a real exit status. The Linux packagers still do not report failure,
  which is unchanged rather than newly claimed.

### Fixed

- **The floating-window branch bound the exclusion dialog under the wrong name**
  (`qt/app.py`): `_setup` assigned `self.excludeDialog`, while every reader —
  `excludeListTriggered` and `qt/tabbed_window.py` — looks for `self.excludeListDialog`.
  Switching `use_tabs` off would therefore have raised `AttributeError`. Latent rather than
  live, since `use_tabs` is hardcoded `True`, so the branch never runs; its sibling
  `ignoreListDialog` is spelled consistently in both branches, which is what this now
  matches. Guarded by source-level tests: the branch cannot be exercised by constructing a
  second `DupeGuru`, because two in one process abort inside Qt's widget teardown.

## [4.7.1] - 2026-08-04

### Added

- **A manual packaging workflow** (`.github/workflows/packaging.yml`, issues #10 and #27):
  `workflow_dispatch` only, since no release has ever shipped a binary and freezing is slow.
  It builds the frozen CLI on Windows and macOS and runs a real content scan through the
  resulting binary, which exercises the `ProcessPoolExecutor` path in
  `core/scanner.py::_hash_files_parallel` — the code path #10 concerns, and previously the
  only way to check it was to have someone build by hand. It fails on the fingerprints of
  that failure mode (`bootstrapping phase`, `falling back to sequential`) as well as on a
  wrong group count, and on the absence of the hashing loop's own progress output —
  without that last check it would pass whether or not the process pool ever ran, which
  is exactly the false confidence it exists to prevent. It cannot check the GUI or the
  NSIS installer; those stay manual.

### Fixed

- **The Windows uninstaller left the entire payload behind** (`setup.nsi`, issue #27):
  measured against a real build, uninstalling removed 3.8 MB of 114.3 MB and stranded
  110.5 MB, the Qt runtime included. The uninstall section enumerated top-level package
  directories, but widening the PyInstaller pin to 6.x moved the whole payload into
  `_internal/`, so every path it named matched nothing and the non-recursive `RMDir` on
  `$INSTDIR` then failed against the surviving tree. It now removes `$INSTDIR\_internal`,
  which covers the packages, the binding and every bundled DLL without naming any of them.
  The pre-6.x paths are kept so uninstalling over a `<= 4.7.0` install still cleans up;
  both layouts were tested. This supersedes the earlier `PyQt5` → `PyQt6` correction to the
  same block, which fixed the directory name but not the layout it lived in.
- **The frozen Windows build bundled the fallback binding** (`package.py`, issue #27):
  `package.py` never set `QT_API`, and qtpy prefers PyQt5 when both bindings are installed,
  so a build machine with both froze PyQt5 while the uninstaller looked for PyQt6. What
  actually stranded in the measurement above was `_internal\PyQt5`, 75.4 MB — fixing the
  layout alone would still have missed. `pin_qt_binding()` now sets `QT_API=pyqt6` before
  each PyInstaller run, making the frozen binding a property of the build rather than of the
  machine; an existing value is honoured so the PyQt5 fallback can still be packaged
  deliberately.
- **`freeze_support()` is called from the entry points** (`run.py`, `cli.py`, `__main__.py`,
  issue #10): it was invoked at import time in `core/pe/matchblock.py` instead. Contrary to
  what #10 states, that call *is* reached in every mode — `core/pe/__init__.py` imports
  `matchblock` eagerly — but reaching it is not enough. It has to run before the entry point
  does anything else, and `run.py` constructs a `QApplication` before the import chain gets
  there. It now sits at the top of each `__main__` block, as the multiprocessing docs
  require, and no longer depends on an import side effect.

### Changed

- **The PyInstaller pin is widened to `>=6.15,<7.0`** (`requirements-extra.txt`): the old
  `>=5.6,<6.0` had no Python 3.14 build, so the whole file — including pytest, flake8 and
  black — could not be installed on a current interpreter. Widened together with actual
  packaging verification rather than on its own: 6.21 was used to build and run a frozen CLI
  on macOS, which is the check that was missing when this was previously left alone.

## [4.7.0] - 2026-08-04

### Changed

- **PyQt6 is now the default Qt binding, with PyQt5 as a supported fallback** (issue #27,
  phase 3b): `requirements.txt` installs PyQt6; `requirements-pyqt5.txt` installs the
  fallback. Nothing in the tree imports a binding directly, so both work unchanged, and CI
  runs a PyQt5 leg — with PyQt6 uninstalled rather than merely overridden by `QT_API` — so
  the fallback cannot rot unnoticed. The full suite passes identically under both, and the
  app was verified to construct, load all resources, build every dialog and run its event
  loop on each.
  **The Linux Qt exclusion is gone.** `requirements.txt` carried
  `PyQt5 ...; sys_platform != 'linux'` because PyQt5 lacked usable Linux wheels; PyQt6 ships
  manylinux wheels, so Linux installs a binding like everything else. The practical effect is
  that the Qt tests now run on the Linux legs, which previously skipped them — the coverage
  gap that let `--full-verify` ship unreachable from the GUI. Qt wheels do not bundle the
  system libraries Qt links against, so CI installs `libegl1`, `libgl1`, `libxkbcommon-x11-0`
  and `libdbus-1-3`; this is documented in the README for bare Linux images generally.
- **Qt is imported through qtpy rather than a binding directly** (`qt/`, `hscommon/`,
  `run.py`, issue #27, phase 3a): 90 import lines across 41 files moved from `PyQt5.*` to
  `qtpy.*`, and the signal/slot names qtpy does not export followed — 25 `pyqtSignal` and 48
  `pyqtSlot` became `Signal` and `Slot`. Behaviour is unchanged: qtpy still resolves PyQt5,
  which stays the only binding in `requirements.txt` for now. Making PyQt6 the default is a
  separate change, split off deliberately so that a failure is attributable to qtpy or to Qt6
  rather than to both at once.
  This also resolves the last Qt5-only spelling in the tree: `qt/recent.py` imported `QAction`
  from `QtWidgets`, which is where it lives in Qt5 and not where it lives in Qt6. qtpy exposes
  it from either module, so no import in the tree is binding-specific any more.
- **Images are embedded in a committed module; the Qt resource build step is gone** (`qt/`,
  `build.py`, issue #27, phase 2): Qt's `.qrc` system needs `pyrcc5` to compile resources
  into a Python module, and PyQt6 ships no equivalent — Riverbank dropped the tool. The
  eleven images now live base64-encoded in `qt/resources_data.py`, generated from
  `qt/dg.qrc` by `python build.py --resources` and **committed**, and are loaded through
  `qt.resources.icon()` / `qt.resources.pixmap()` instead of `QPixmap(":/name")`.
  Embedding rather than reading `images/` off disk preserves the property that made frozen
  builds work: the bytes live inside a Python module, so nothing has to be bundled as data
  files — `package.py` copies only two logos, and changing that lands on the packaging work
  that cannot be verified here (#10). Committing rather than generating during the build
  removes the failure mode from #50 outright: there is no longer a resource step that can
  fail silently. `qt/tests/resources_test.py` regenerates and compares, so the committed
  copy cannot drift from `images/` unnoticed, and because it needs no Qt bindings it runs on
  the Linux legs too — the first Qt-adjacent coverage those have had. The generated module
  is 124 KB, against 347 KB for the `dg_rc.py` it replaces. `pyqt5-dev-tools` is no longer a
  build prerequisite, and `hscommon.build.fix_qt_resource_file`, which existed only to patch
  `pyrcc5` output, is removed.
- **The Qt code now uses Qt6-compatible spellings throughout** (`qt/`, issue #27, phase 1):
  346 enum references moved to their scoped form (`Qt.AlignLeft` to
  `Qt.AlignmentFlag.AlignLeft`), `exec_()` to `exec()`, `Qt.MidButton` to
  `Qt.MouseButton.MiddleButton`, `QFileDialog.DirectoryOnly` to `FileMode.Directory` plus
  the `ShowDirsOnly` option, and the `QDesktopWidget` uses to `QGuiApplication.screenAt` —
  the idiom `qt.util.move_to_screen_center` already used. Every one of these works
  identically on PyQt5 5.15 and PyQt6, so this is a no-op for the shipped binding and
  removes almost all of the eventual port's diff. The mapping was derived by introspecting
  PyQt6 rather than written by hand; every rewrite was checked to have the same value under
  PyQt5 and to resolve under PyQt6, and all 380 enum spellings in the tree now resolve under
  both. `qt/recent.py` still imports `QAction` from `QtWidgets`, which is deliberate: it
  moved to `QtGui` in Qt6 and no single import works on both.

### Added

- **Smoke coverage for the Qt front end** (`qt/tests/`, issue #27, phase 0): nothing under
  `qt/` was imported by any test and CI ran only `core hscommon`, which is how
  `--full-verify` shipped unreachable from the GUI and how an empty `qt/dg_rc.py` produced an
  icon-less build that reported success. Covers widget construction, preferences reaching the
  scan options, and resource aliases actually resolving. CI now runs `pytest core hscommon qt`
  plus a resource-build step on the platforms that have `pyrcc5`; the Linux legs skip the Qt
  tests, since `requirements.txt` excludes PyQt5 there.

## [4.6.0] - 2026-08-04

### Added

- **`--plan` reports what a deletion would do** (`cli.py`, `core/app.py`, issue #25): there
  was no way to see what `--delete` would actually remove. `--dry-run` gave a file count and
  a byte total, but could not say how many files would be refused by the size/mtime
  revalidation, how many matched only on a sampled hash, or how many sit on a different
  volume from their reference. `--plan` implies no mutation, needs no `--delete`, prints
  that summary to stderr, and emits a per-file plan as JSON to stdout carrying
  `would_delete`, `match_confidence` and a `blocked_reason`. It works against a saved
  results file too, which is where it matters most: results from last week may describe a
  directory that has since changed. `--dry-run`'s summary gained the group and
  full-content counts.
  The plan is computed by the deletion's own predicate rather than a parallel
  reimplementation, so the paths it says will go are exactly the ones a subsequent
  `--delete` removes; a plan that could disagree with the deletion would be worse than none.
- **Partial-hash matches are recorded and can be verified** (`cli.py`, `core/scanner.py`,
  `core/results.py`, issue #26): `--partial-hash-threshold` matches large files on three
  sampled chunks, which can produce false positives, but the result set did not carry the
  distinction — a sampled match and a full-content match both scored 100%. Each duplicate
  entry now carries `partial_match`, and each stats record a `partial_matches` count, in
  both JSON and NDJSON. New `--full-verify` re-reads only the files involved in partial
  matches, compares them in full, drops any pair that does not actually match, and clears
  the `partial` flag on those that do — so verified matches need no `--allow-partial-matches`
  to delete. The results XML written by the GUI now round-trips the flag as well; it was
  previously dropped on save, which silently promoted probable duplicates to confirmed ones
  across a save/load cycle.
- **CLI exclusions and ignore list** (`cli.py`, issue #24): the CLI had no way to express
  either, so a scripted scan walked `node_modules`, `.venv`, `__pycache__` and every OS
  metadata directory, with `.git` skipped only by an incidental dot-prefix fallback. New
  `--exclude REGEX` (repeatable), `--exclude-from FILE` (one regex per line, `#` comments
  ignored), `--exclude-defaults` (the same set as the GUI's Restore Defaults), and
  `--ignore-list FILE` to load an `ignore_list.xml` saved by the GUI. All four drive the
  existing `ExcludeList` and `IgnoreList`, so matching behaves identically to the GUI.
  Invalid regexes, over-broad patterns that `core.exclude` forbids, and unreadable files are
  reported and exit 2 rather than being silently skipped — `IgnoreList.load_from_xml`
  swallows every exception and returns silently, so the CLI validates the path itself and
  warns when a list loads no entries.
  **Note:** adding any exclusion replaces the built-in "skip dot-prefixed folders" fallback,
  so `--exclude` on its own *widens* the scan. `--exclude-defaults` restores it. This is
  documented in `--exclude`'s help text and covered by a test.

- **`--full-verify` is reachable from the GUI** (`qt/`, issue #26): the preferences dialog
  could enable partial hashing, which is what creates the false-positive risk, but had no
  way to enable the verification that resolves it — the option existed only on the CLI. Adds
  a "Verify partially hashed matches by comparing full contents" checkbox, persisted as
  `FullVerify`. It is gated on partial hashing being enabled, since with no partial matches
  the verification pass is a no-op.

### Changed

- **The pre-deletion revalidation lives in one place** (`core/app.py`, issue #25): three
  copies of "is this file still safe to delete" existed — `_do_delete_dupe` raising,
  `_delete_from_saved_results` collecting problems, and nothing at all on the planning side.
  Extracted as `check_deletable`, which returns a `DeleteStatus` and leaves the policy to
  each caller: the live path still treats a vanished file as a silent no-op, the
  saved-results path still reports it. No behaviour change.
- CLI skip reasons no longer read `skipped <path>: skipped: ...` — the reason strings carried
  a prefix that both consumers were already supplying.

### Fixed

- **A missing `pyrcc5` no longer produces an icon-less GUI and calls it a success**
  (`build.py`): the Qt resource step shelled out to a bare `pyrcc5` with the output
  redirected by the shell. `build.py` is routinely run as `./env/bin/python build.py`
  without the venv activated, in which case `pyrcc5` is not on PATH — but the shell still
  created `qt/dg_rc.py`, empty, and the build reported "build succeeded". The result was a
  GUI that started fine with every icon missing and nothing anywhere to explain why. The
  compiler is now looked up beside the running interpreter first, invoked via `subprocess`
  rather than a shell redirect, and an empty result is a hard error.
- **`--from-results` deletions now honour the partial-match gate** (`cli.py`, issue #26):
  the scan path refuses to delete files matched only on a sampled hash unless
  `--allow-partial-matches` is passed, but routing the same deletion through
  `--from-results` bypassed that check entirely, because the flag was never serialised.
  A results file written before this change cannot be checked at all; deleting from one
  now warns explicitly rather than reporting zero partial matches, which would have been
  indistinguishable from a genuinely clean result.
- **`Scanner._getmatches` no longer rewrites `self.scan_type`** (`core/scanner.py`, issue #14):
  it assigned `self.scan_type = ScanType.FIELDS` to smuggle a `no_field_order` flag through,
  permanently. A second `get_dupe_groups()` call on the same `Scanner` therefore did an
  *ordered* field comparison and found nothing where the first found a match — 0 groups
  instead of 1 in the new regression test. It also made the `FIELDSNOORDER` branch further
  down unreachable. Both front ends build a fresh `Scanner` per scan so nothing hit this
  today, but the `--watch` and resumable-scan work in #28 naturally would.
- **A file that vanishes mid-scan no longer logs as a decoding error** (`core/fs.py`,
  issue #15): `FilesDB.get` and `put` called `path.stat()` above their `try`, so a
  `FileNotFoundError` escaped into `File.__getattribute__`'s broad handler, which reports
  every exception as *"error while decoding"*. That sends the reader looking in the wrong
  place entirely, and the attribute silently fell back to `b""`. Scanning a folder another
  process is writing to — a download directory, a syncing cloud folder — hits this routinely.
- **`FilesDB.close()` now clears the connection** (`core/fs.py`, issue #16): it left a closed
  connection in place, so every later call raised *"Cannot operate on a closed database"* into
  the broad handler in `get()`, logging a warning per file. The public methods now guard on
  `conn is None` and `connect()` closes any previous connection instead of leaking it, all
  matching what `HashCache` already did.

### Changed

- **`--filter-hardlinks` now defaults off, matching the GUI** (`cli.py`, issue #21): the CLI
  defaulted it *on* while the GUI defaults `ignore_hardlink_matches` off, so the same folders
  scanned through the two front ends returned different results and nothing said so. The CLI
  now matches the GUI; pass `--filter-hardlinks` to opt in. **This changes CLI behaviour**:
  scripts relying on the old default need the flag added.
- **`--rehash-ignore-mtime` renamed to `--trust-cache-ignore-mtime`** (`cli.py`, issue #21):
  the old name and its help text ("Always rehash files even if their modification time is
  unchanged") described the opposite of the effect. The flag sets `FilesDB.ignore_mtime`,
  which drops `mtime_ns` from the cache lookup and so makes hits *more* likely — fewer
  rehashes, and specifically reuse of a cached digest for a file edited without changing size.
  The old spelling still works as an alias.

### Fixed

- **Declared Python support is now consistent** (`setup.cfg`, `tox.ini`, issue #22):
  `python_requires` said `>=3.7`, classifiers stopped at 3.10, `tox.ini` listed py37–py311,
  and CI tested 3.10–3.14. 3.7 was claimed by three of them and tested by none, and the code
  cannot import below 3.10. All sources now say 3.10–3.14. `tests_require` also said
  `pytest >=6,<7` while `requirements-extra.txt` said `>=7,<8`; the two ranges did not overlap.

### Removed

- **`.tx/`** (issue #31): the Transifex client config targeted the upstream maintainer's
  project (`voltaicideas/dupeguru-1`). The workflow that used it automatically is long gone,
  but a developer running `tx push` would still have written into somebody else's resource.
  This fork does not manage translations.

### Security

- **`pre-commit/action` is now SHA-pinned** (`.github/workflows/default.yml`, issue #31): it
  was the only action referenced by mutable tag while every other action in both workflows is
  pinned to a full commit SHA. That job runs over the whole checkout.

## [4.5.0] - 2026-08-03

Minor rather than patch: `--dry-run` changes behaviour, `--allow-partial-matches` is new, and
`python -m dupeguru` starts working.

The headline is the deletion path. 4.4.x could destroy a file and leave nothing in its place on
a default Windows install, and report it as a success.

### Fixed (safety)

- **Delete-and-replace-with-link no longer destroys the file when the link cannot be made**
  (`core/app.py`, issue #20): `_do_delete_dupe` deleted the duplicate first and only then
  tried to create the replacement link. On Windows, creating a symlink requires Developer
  Mode or `SeCreateSymbolicLinkPrivilege`, neither of which is on by default — so the
  expected outcome on a stock install was: file trashed (or permanently deleted with
  `--direct-delete`), no link created, and the error swallowed so `perform_on_marked`
  recorded a **success**. The results table showed the operation as having worked. The link
  is now built at a temporary name beside the original *before* anything is deleted; if it
  cannot be created the original is untouched and the error is recorded as a problem with
  the file left marked. `os.link` was not wrapped at all previously, so a cross-device
  hardlink failed the same way. The Windows privilege message is now raised rather than
  pushed through `view.show_message`, which was being called from the job's worker thread,
  once per file. Directory symlinks now pass `target_is_directory`, which folder-mode scans
  need on Windows.

### Fixed

- **Hash caches now record which algorithm produced a digest** (`core/fs.py`,
  `core/hash_cache.py`, issue #13): both caches store 16-byte digests and both fall back from
  xxhash to md5 when xxhash is unavailable, but neither recorded which had been used. If
  xxhash availability changed between runs — a different venv, a frozen build missing the
  wheel, a platform without one — unmodified files returned digests from the old algorithm
  while new files got the current one, and byte-identical files silently stopped being
  reported as duplicates. Nothing about a stored digest reveals which function made it, so
  there was no way to notice. Both caches now write a `hash_algorithm` marker and discard
  every cached digest when it changes. Caches written before this change carry no marker and
  are discarded once, costing a single rehash.

- **CSV export wrote malformed line endings on Windows** (`core/export.py`): `export_to_csv`
  opened the file without `newline=""`, so the `\r\n` written by the `csv` module was
  translated again by the text layer, producing `\r\r\n`. Strict CSV parsers read that as a
  blank line between every row. Found by writing the first tests for this module. The file
  handle was also never closed; both exports now use a context manager.

- **`dupeguru-scan --help` no longer crashes on a Windows console** (`cli.py`, issue #29): the
  `--scan-type` help text contained U+2192 (`→`), which cp1252 cannot encode, so argparse
  raised `UnicodeEncodeError` while printing usage. `--help` is the first thing anyone runs.
  All non-ASCII is gone from `cli.py`, and stdout/stderr are reconfigured to UTF-8 with
  `errors="replace"` so a path containing characters outside the console code page cannot
  abort a scan either.
- **Documented CLI invocations now work** (`cli.py`, `__main__.py`, issue #30): the module
  docstring advertised a `scan` subcommand that does not exist — it was parsed as a folder
  name, producing "folder does not exist" for a path the user never typed — and `prog` was
  set to `"dupeguru scan"`, repeating the claim in `--help`. `python -m dupeguru` raised
  `ModuleNotFoundError` because the checkout directory was not on `sys.path`. The docstring
  and `prog` now match reality, and `__main__.py` puts its own directory on `sys.path`.
- **`__main__.py` no longer runs the CLI at import time** (`__main__.py`): it called
  `sys.exit(main())` unguarded. Spawn-based pool workers re-import the main module, so every
  worker would have re-run the whole CLI. Now behind `if __name__ == "__main__":`.

- **Loading a directory list no longer discards the exclusion list** (`core/app.py`,
  `core/directories.py`, issue #12): `load_directories()` reset the selection by calling
  `self.directories.__init__()`. `Directories.__init__` takes `exclude_list` as an argument
  defaulting to `None`, so calling it with no arguments replaced the configured exclusion list
  with nothing. Every scan afterwards ignored the user's exclusions — no error, no UI
  indication — until the app was restarted. Replaced with an explicit `Directories.clear()`
  that resets the selection and states while leaving the exclusion list alone.
- **"Clear Cache" now clears the cache scans actually use** (`core/hash_cache.py`,
  `core/app.py`, issue #11): `clear_hash_cache()` cleared only `fs.filesdb`, while the
  content-scan fast path in `core/scanner.py` reads `hashcachedb` (`hash_cache2.db`). That
  second cache had no `clear()` method at all, so clearing to work around a suspect digest did
  nothing to it.
- **The scan hash cache no longer grows without bound** (`core/hash_cache.py`, issue #11): it
  had no purge of any kind, so every file ever hashed stayed forever, including files deleted
  long ago. `HashCache` gains `purge_missing()`, `purge_old_entries()` and a throttled
  `purge_if_stale()` mirroring `FilesDB`, now called from both the GUI and CLI scan paths.

### Added

- **`core/gui/mark_dialog.py` coverage 0% → 100%** (`core/tests/mark_dialog_test.py`,
  issue #23): the rule-based auto-marking engine decides which file in each group is kept and
  marks the rest, so whatever it marks is what a later delete removes. It had no tests at all.
  Covers rule-list construction, `apply()` marking every non-keeper, never marking the group
  reference, idempotency, replacing rather than adding to a previous marking, honouring the
  selected rule, reference-folder files never being marked or displaced, and opposite size
  rules genuinely picking opposite keepers.
- **`core/export.py` coverage 26% → 100%** (`core/tests/export_test.py`): XHTML structure and
  indentation, CSV header and quoting, UTF-8 in both, empty input, and the `OSError` that
  `core/app.py` relies on catching.
- **`core/tests/hash_cache_test.py`**: `HashCache` previously had no tests — `cache_test.py`
  covers the picture cache, not this one. Covers the round trip, clear, all three purge paths,
  throttling, schema rebuild, and hash-algorithm invalidation.
- **`HashCache` schema versioning** (`core/hash_cache.py`): an `entry_dt` column was needed for
  age-based purging, so the table now carries a schema version in a `meta` table and is dropped
  and rebuilt on mismatch. Rebuilding costs one rehash, which is cheaper than a migration for a
  cache. This is also the mechanism the hash-algorithm marker above uses.
- **Coverage configuration** (`setup.cfg`): `[coverage:run]` now omits test code and
  build/localisation tooling, so the headline figure reflects shippable code. This *lowers* the
  reported number from 84% to 78% — the previous figure was inflated by test files, which are
  ~100% covered by construction.

## [4.4.1] - 2026-08-03

Patch release carrying a single fix, cut promptly because 4.4.0 shipped with the bug live: any
release published with a human-readable title would break the update check for anyone running
4.4.0.

> **Release convention:** GitHub release *titles* in this project must be bare semver
> (`4.4.1`, not `v4.4.1 - some description`). 4.4.0 and earlier parse the release name rather
> than the tag, so a descriptive title breaks the update check for those builds. This
> constraint can be dropped once no one is running 4.4.0.

### Fixed

- **`check_for_update` no longer crashes on a free-form release title** (`core/util.py`,
  issue #19): it parsed `release["name"]` as semver with no guard, and a release name is
  arbitrary text on GitHub. This was not theoretical — publishing v4.4.0 with the title
  `"v4.4.0 - first release of the fork"` broke the update check immediately, which would have
  crashed the About box for anyone running that build. Version now comes from `tag_name`
  (with an optional `v` prefix stripped), falling back to `name`, and unparseable releases are
  skipped rather than raising. Also guards a non-semver `current_version`, a non-list API
  payload, malformed entries, and a missing `html_url`, and replaces the deprecated
  `logging.warn` with `logging.warning`.
- **`core/util.py` test coverage 32% → 99%**: added `core/tests/util_test.py`, which the module
  previously had none of. Covers version comparison, prerelease filtering, and every error path
  with `urlopen` mocked, so no network access is needed.

## [4.4.0] - 2026-08-03

First release of the [haggyroth fork](https://github.com/haggyroth/dupeguru). Everything below
accumulated since forking from `arsenetar/dupeguru` at 4.3.1, which is the last upstream
release this shares a version with.

Headline: three ways the tool could delete data the user had not agreed to lose are fixed, the
fork no longer routes anyone or anything upstream, and CI runs for the first time.

### Fixed (safety)

- **The scan walk no longer follows directory links** (`core/directories.py`, issue #8):
  `_get_files` called `os.DirEntry.is_dir()`, which follows symlinks by default, so the file
  collection descended through links. A cycle became unbounded recursion, and a link pointing
  out of the selected folders pulled foreign files in as deletion candidates — those files are
  ordinary files at their real location, so the symlink guard in `_do_delete_dupe` did not
  protect them. Symlinked *files* were already excluded by `File.can_handle`, and
  `fs.Folder.subfolders` already guarded the folder-scan path; the file-scan path now matches.
  Windows **directory junctions** are covered too: Python reports `is_symlink()` False and
  `is_dir(follow_symlinks=False)` True for a junction, so excluding symlinks alone would have
  left Windows — the platform where junctions need no privilege to create — still exposed.
  Uses `os.DirEntry.is_junction()` on Python 3.12+, falling back to the cached
  `FILE_ATTRIBUTE_REPARSE_POINT` attribute on 3.10/3.11.

- **`--dry-run` no longer permits deletion** (`cli.py`, issue #7): the flag was parsed and then
  never read again, so `--delete --yes --dry-run` deleted files. It now takes precedence over
  `--delete` on both the scan and `--from-results` paths, reports what would be removed, and
  removes nothing. It also no longer requires `--yes`, since a dry run is safe by definition.
- **CLI deletion no longer silently removes partial-hash matches** (`cli.py`, issue #9): the
  GUI warns before deleting files matched on a sampled hash, but `_delete_dupes` bypassed
  `delete_marked()` and so never ran that check. `--delete` now refuses when any marked file
  was matched partially, and requires the new `--allow-partial-matches` to proceed.
- **`_HeadlessView.ask_yes_no` now fails closed** (`cli.py`): it returned `True`
  unconditionally, so any safety prompt core asked would be auto-accepted without the user
  seeing it. It now declines and logs to stderr; deliberate confirmation goes through explicit
  flags. Note this means a future core-side prompt blocks the CLI rather than being waved
  through, which is the intended direction.

### Added

- **Dependabot** (`.github/dependabot.yml`): weekly update checks for the `pip` and
  `github-actions` ecosystems, with minor/patch updates grouped into a single PR.
- **Coverage in CI** (`.github/workflows/default.yml`): the test job now runs under
  `pytest-cov` and uploads `coverage.xml` as a build artifact. `pytest-cov` added to
  `requirements-extra.txt`.
- **CLI documentation** (`README.md`): usage, output formats, exit codes, and deletion
  semantics for `dupeguru-scan`, which previously had no coverage in the docs.

### Changed

- **Fork identity**: this repository no longer points users at the upstream project.
  - `run.py`: the crash-report dialog now links to this fork's issue tracker instead of
    `arsenetar/dupeguru/issues`.
  - `core/util.py`: `check_for_update` now queries this fork's releases. It previously
    offered upstream releases as updates to a build that is not upstream.
  - `setup.nsi`: installer `HELPURL` repointed to this fork.
  - `setup.cfg`: project `url` and `Bug Reports` repointed to this fork; upstream retained as
    a separate `Upstream` project URL for attribution.
  - `README.md`, `CONTRIBUTING.md`: fork status stated explicitly, issue links repointed.
- **Lint backlog cleared**: `black` applied across 19 files and all 43 `flake8` errors fixed
  (dead imports, unused locals, ambiguous `l` loop variables). The `pre-commit` CI job gates
  the `test` job via `needs:`, so this was blocking the entire pipeline. `pre-commit run
  --all-files` now passes all six hooks.
- **CI matrix trimmed to Python 3.10+** (`.github/workflows/default.yml`): 3.8 and 3.9 were
  listed but cannot import the package — `core/hash_cache.py` uses PEP 604 unions
  (`tuple[str, bytes] | None`) in function signatures that evaluate eagerly on Python <= 3.13,
  with no `from __future__ import annotations`. Note `setup.cfg` and `tox.ini` still declare
  3.7; reconciling them is tracked in issue #22.
- **First green CI**: Actions had never executed on this fork (zero runs). The full matrix and
  CodeQL now pass, with no open code-scanning alerts.

### Removed

- **`.github/workflows/tx-push.yml`**: pushed `locale/*.pot` to the upstream Transifex project
  (`voltaicideas/dupeguru-1`) on every push to `master`. A live write path into an upstream
  resource has no place in a fork.
- **`.github/FUNDING.yml`**: routed sponsorship for this fork to the upstream author.

### Security

- **Shell injection fix** (`core/app.py`): `invoke_custom_command` now uses `shlex.split` +
  `Popen(argv, shell=False)` instead of a raw shell string. Paths containing shell
  metacharacters (`; | & $()`) can no longer execute arbitrary commands.

### Fixed

- **`os.path.samefile` received lowercased path** (`core/scanner.py`): on case-sensitive
  filesystems the dedup-by-path check passed the normalized (lowercased) path to `samefile`
  instead of the original, causing valid duplicates to be dropped.
- **`ExcludeList._remove_compiled` substring collision** (`core/exclude.py`): unmark `"^a"`
  would also silently remove `"^abc"` because the old code used `str.startswith`. Now uses
  exact pattern equality.
- **Parallel hasher defeats big-file sampling** (`core/scanner.py`): when a file exceeded
  `big_file_size_threshold`, the hash cache pre-populate step was setting all three digest
  fields to the full hash, bypassing the partial/sample optimisation. Only `digest` is now
  set for big files; `digest_partial` and `digest_samples` are left `None` so
  `getmatches_by_contents` computes them correctly.
- **`getmatches` temp DB file leak on Windows** (`core/engine.py`): the seen-pairs SQLite
  file created by `getmatches` was never cleaned up when the process exited abnormally.
  Replaced `mkstemp` with `TemporaryDirectory`; added `gc.collect()` on Windows to release
  the file lock before `shutil.rmtree` runs.
- **`getmatches` seen-pairs key collision** (`core/engine.py`): the seen-pairs DB keyed on
  `str(o.path)`, so multiple objects with the same path (common in tests and theoretically
  possible in scans) collapsed to a single entry, causing pairs to be skipped. Keys now use
  `str(id(o))` (object identity), restoring the original semantics.
- **Dead `gnu0` platform branch** (`core/fs.py`): removed an unreachable `if
  platform.startswith("gnu0")` branch that also imported `sys.platform` under the wrong name.
- **`results.load_from_xml` unbounded recursion** (`core/results.py`): the `do_match` helper
  was recursive with depth proportional to group size; replaced with a flat double loop to
  avoid `RecursionError` on large saved result files.
- **`FilesDB` WAL pragma missing** (`core/fs.py`): hash cache DB was using the default
  journal mode, causing one fsync per `put()` call. Now opens in WAL + NORMAL synchronous
  mode and batches writes, committing at `_BATCH_SIZE` (500) rows or on explicit `commit()`.
- **Picture cache corruption recovery crashes on `:memory:`** (`core/pe/cache_sqlite.py`):
  if `_check_upgrade` raised on an in-memory cache, the recovery path called
  `os.remove(":memory:")`, raising `FileNotFoundError` and masking the original error.
  The removal is now skipped for in-memory caches; an `OSError` from a failed on-disk
  removal re-raises the original `DatabaseError`.
- **`Directories.__contains__` case-insensitive collision** (`core/directories.py`): on
  case-insensitive filesystems (Windows, macOS) the same folder could be added twice because
  `Path("C:/Foo") != Path("C:/foo")`. The check now normalises with `os.path.normcase`.
- **`os.symlink` on Windows gives cryptic error** (`core/app.py`): when "link deleted files"
  is enabled with symbolic links, a missing `SeCreateSymbolicLinkPrivilege` raised an opaque
  `OSError`. The error is now caught and a user-friendly message is shown suggesting
  Developer Mode or hardlinks.
- **`Folder.subfolders` non-deterministic order** (`core/fs.py`): `scandir` returns entries
  in filesystem order (undefined). `subfolders` now sorts by path so callers see a consistent
  order across platforms.

### Performance

- **Parallel hash caching fallback** (`core/scanner.py`): a single worker failure previously
  killed the entire `ProcessPoolExecutor`. Individual future failures are now caught
  per-future; only those files are retried sequentially. A pool-level failure falls back to
  fully sequential hashing using only the files that did not already complete in parallel.
- **`FilesDB.purge_missing` / `purge_old_entries` run on every scan** (`core/app.py`,
  `core/fs.py`): both full-table-scan purges now run at most once every 7 days, controlled
  by a timestamp stored in a new `meta` table.
- **`purge_outdated` does one `os.stat` per cached picture** (`core/pe/cache_sqlite.py`):
  replaced per-file `os.stat` with a per-directory `os.scandir` pass. Syscall count drops
  from O(n) individual stats to O(d) scandir calls where d = number of unique directories.
  `purge_outdated` is also skipped entirely when the cache is opened `readonly=True`.
- **`engine.merge_similar_words` O(n²) `keys.remove`** (`core/engine.py`): `list.remove` is
  O(n) and was called inside the main loop. Replaced with a `removed: set` for O(1) skip
  checks.
- **`engine.compare` O(n) list copies** (`core/engine.py`): replaced `second[:]` +
  `second.remove` (O(n) per call) with `Counter(second)` for O(1) membership test and
  decrement.

### Changed

- **`GETMATCHES_LIMIT`** (`core/engine.py`): the hardcoded `LIMIT = 5_000_000` inside
  `getmatches` is now a public module-level constant `GETMATCHES_LIMIT` so tests and
  integrators can override it without monkey-patching.
- **`Scanner.parallel_scan`** (`core/scanner.py`): moved from a class-level attribute
  evaluated at import time to an instance attribute set in `__init__`, so CPU count is read
  at construction time rather than at module import.
- **`get_groups` orphan recursion** (`core/engine.py`): the orphan-match pass was recursive;
  replaced with an iterative loop to avoid stack overflow on degenerate match graphs.
- **Picture cache rowid SQL** (`core/pe/cache_sqlite.py`): `get_multiple` and
  `purge_outdated` now use `?` placeholders for rowid `IN (...)` lists instead of
  string-formatted integer lists.
- **`ExcludeList._do_compile`** (`core/exclude.py`): replaced the hand-rolled unbounded
  `memoize` dict with `@staticmethod @functools.lru_cache(maxsize=1024)`.

---

## Earlier history

See `git log` for changes prior to this changelog.

[Unreleased]: https://github.com/haggyroth/dupeguru/compare/v4.7.1...HEAD
[4.7.1]: https://github.com/haggyroth/dupeguru/compare/v4.7.0...v4.7.1
[4.7.0]: https://github.com/haggyroth/dupeguru/compare/v4.6.0...v4.7.0
[4.6.0]: https://github.com/haggyroth/dupeguru/compare/v4.5.0...v4.6.0
[4.5.0]: https://github.com/haggyroth/dupeguru/compare/v4.4.1...v4.5.0
[4.4.1]: https://github.com/haggyroth/dupeguru/compare/v4.4.0...v4.4.1
[4.4.0]: https://github.com/haggyroth/dupeguru/releases/tag/v4.4.0
