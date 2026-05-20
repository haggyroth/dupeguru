# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
