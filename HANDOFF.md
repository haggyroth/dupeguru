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
| Version | 4.5.0, released |
| Releases | v4.4.0, v4.4.1, v4.5.0 |
| Issues | 21 closed, 5 open |
| Tests | 787 passing on Windows; expect **788 passing, 6 skipped** on macOS (see below) |
| CI | green on Python 3.10–3.14 (Linux) plus Windows and macOS |

Work is tracked as GitHub issues on the fork. Don't keep a parallel roadmap file — check the
tracker.

## Setup on the MacBook

```bash
git clone https://github.com/haggyroth/dupeguru.git
cd dupeguru
python3 -m venv env && source env/bin/activate
pip install -r requirements.txt -r requirements-extra.txt
pip install pre-commit && pre-commit install
python build.py --modules      # builds the C extensions
pytest core hscommon
```

Python **3.10+** is required — `core/hash_cache.py` uses PEP 604 unions in signatures that
are evaluated at import time, so 3.8 and 3.9 cannot import the package at all. `macos.md` has
the Qt/Homebrew setup if you need to run the GUI rather than just the CLI and tests.

## What changes when you move to macOS

This matters more than it sounds — several tests are platform-gated, so the pass/skip counts
move and a green run looks different.

| Test group | Windows (local) | macOS | Linux CI |
|---|---|---|---|
| 4 POSIX symlink tests | **skip** — no `SeCreateSymbolicLinkPrivilege` | run | run |
| 3 Windows junction tests | run | **skip** | **skip** |
| 1 case-sensitivity test | skip | skip (APFS is case-insensitive) | run |
| 2 exclude union-mode tests | skip | skip | skip |
| **Totals** | 787 / 7 skipped | **788 / 6 skipped** | 789 / 5 skipped |

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
- **[#25](https://github.com/haggyroth/dupeguru/issues/25)** — `--plan` mode. Cheap now:
  `--dry-run` already computes a deletion plan, this is mostly surfacing more of it.
- **[#26](https://github.com/haggyroth/dupeguru/issues/26)** — surface partial-hash matches in
  output. Also cheap: `Match.partial` already exists and is already gated on for deletion, it
  just isn't serialised.
- **[#27](https://github.com/haggyroth/dupeguru/issues/27)** — PyQt6 alongside PyQt5. A real
  project. Relevant on macOS, where Homebrew increasingly prefers PyQt6.
- **[#28](https://github.com/haggyroth/dupeguru/issues/28)** — resumable scans. The largest
  item; the hash cache already delivers most of the practical benefit.

## Context worth carrying

The original goal was to make this safe to point at a production folder. Three bugs made it
unsafe and are now fixed and released in 4.5.0:

- `--dry-run` was parsed and never read, so `--delete --yes --dry-run` deleted files
- the scan walk followed directory symlinks and Windows junctions, so it could recurse without
  bound and collect files outside the selected folders
- delete-and-replace-with-link deleted the file *before* trying to create the link, and
  swallowed the failure — on a default Windows install that meant file gone, no link, reported
  as a success

If you do run this on real data: `--dry-run` genuinely works now, and `--exclude` is worth
using — but note that adding any exclusion *replaces* the built-in "skip dot-prefixed folders"
fallback, so pass `--exclude-defaults` alongside it or the scan will start descending into
`.git`.
