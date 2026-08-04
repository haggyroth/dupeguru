# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
- **Coverage configuration** (`setup.cfg`): `[coverage:run]` now omits test code and
  build/localisation tooling, so the headline figure reflects shippable code. This *lowers* the
  reported number from 84% to 78% — the previous figure was inflated by test files, which are
  ~100% covered by construction.

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

- **`HashCache` schema versioning** (`core/hash_cache.py`): an `entry_dt` column was needed for
  age-based purging, so the table now carries a schema version in a `meta` table and is dropped
  and rebuilt on mismatch. Rebuilding costs one rehash, which is cheaper than a migration for a
  cache. This also gives issue #13 the mechanism it needs to record which hash algorithm
  produced a digest.
- **`core/tests/hash_cache_test.py`**: `HashCache` previously had no tests — `cache_test.py`
  covers the picture cache, not this one. 19 tests covering the round trip, clear, all three
  purge paths, throttling, and schema rebuild.

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

[Unreleased]: https://github.com/haggyroth/dupeguru/compare/v4.4.1...HEAD
[4.4.1]: https://github.com/haggyroth/dupeguru/compare/v4.4.0...v4.4.1
[4.4.0]: https://github.com/haggyroth/dupeguru/releases/tag/v4.4.0
