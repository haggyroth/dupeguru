# dupeGuru

[dupeGuru][dupeguru] is a cross-platform (Linux, OS X, Windows) tool to find duplicate files in
a system. It is written mostly in Python 3 and uses [qt](https://www.qt.io/) for the UI.

## About this fork

This is a personal fork of [arsenetar/dupeguru][upstream], maintained at
[haggyroth/dupeguru][fork]. Upstream is no longer actively maintained; this fork exists to
carry fixes and features we want for our own use.

**All issues, pull requests, and discussion belong on [this fork][fork-issues].** Nothing here
is intended to be contributed upstream. If you are looking for the original project, follow the
[upstream link][upstream] instead.

Changes in this fork over upstream include large-scan performance work (parallel hashing,
BK-tree photo matching, WAL-mode caches), safety guards around deletion, rule-based marking,
and a headless [command-line interface](#command-line-interface).

## Contents of this folder

This folder contains the source for dupeGuru. Its documentation is in `help`, but is also
[available online][documentation] in its built form. Here's how this source tree is organized:

* core: Contains the core logic code for dupeGuru. It's Python code.
* qt: UI code for the Qt toolkit. It's written in Python and uses PyQt.
* images: Images used by the different UI codebases.
* pkg: Skeleton files required to create different packages
* help: Help document, written for Sphinx.
* locale: .po files for localization.
* hscommon: A collection of helpers used across HS applications.

## How to build dupeGuru from source

### Windows & macOS specific additional instructions
For windows instructions see the [Windows Instructions](Windows.md).

For macos instructions (qt version) see the [macOS Instructions](macos.md).

### Prerequisites
* [Python 3.10+][python] — parts of the codebase use PEP 604 (`X | None`) annotations that are
  evaluated at runtime, so earlier versions will not import.
* PyQt5

### System Setup
When running in a linux based environment the following system packages or equivalents are needed to build:
* python3-pyqt5
* pyqt5-dev-tools (on some systems, see note)
* python3-venv (only if using a virtual environment)
* python3-dev
* build-essential

Note: On some linux systems pyrcc5 is not put on the path when installing python3-pyqt5, this will cause some issues with the resource files (and icons). These systems should have a respective pyqt5-dev-tools package, which should also be installed. The presence of pyrcc5 can be checked with `which pyrcc5`.  Debian based systems need the extra package, and Arch does not.

To create packages the following are also needed:
* python3-setuptools
* debhelper

### Building with Make
dupeGuru comes with a makefile that can be used to build and run:

    $ make && make run

### Building without Make

    $ cd <dupeGuru directory>
    $ python3 -m venv --system-site-packages ./env
    $ source ./env/bin/activate
    $ pip install -r requirements.txt
    $ python build.py
    $ python run.py

### Generating Debian/Ubuntu package
To generate packages the extra requirements in requirements-extra.txt must be installed, the
steps are as follows:

    $ cd <dupeGuru directory>
    $ python3 -m venv --system-site-packages ./env
    $ source ./env/bin/activate
    $ pip install -r requirements.txt -r requirements-extra.txt
    $ python build.py --clean
    $ python package.py

This can be made a one-liner (once in the directory) as:

    $ bash -c "python3 -m venv --system-site-packages env && source env/bin/activate && pip install -r requirements.txt -r requirements-extra.txt && python build.py --clean && python package.py"

## Command-line interface

This fork ships a headless CLI for scripted and automated scans. It is installed as the
`dupeguru-scan` console script, and can also be run directly from a source checkout:

    $ dupeguru-scan <folder> [<folder> ...] [options]
    $ python cli.py <folder> [<folder> ...] [options]
    $ python -m dupeguru <folder> [<folder> ...] [options]

There is no `scan` subcommand — folders are positional arguments.

Scan a folder and write JSON results:

    $ dupeguru-scan ~/Photos --output results.json

Stream newline-delimited JSON for large result sets:

    $ dupeguru-scan /data --ndjson | jq 'select(.type == "group")'

Machine-readable progress on stderr, results on stdout:

    $ dupeguru-scan /data --ndjson --progress-json > results.ndjson

Re-use a previous scan instead of rescanning:

    $ dupeguru-scan --from-results results.json

### Exclusions

Without exclusions the scan walks everything under the given folders, `node_modules` and
`.venv` included:

    $ dupeguru-scan ~/code --exclude '^node_modules$' --exclude '^\.venv$'
    $ dupeguru-scan ~/code --exclude-from excludes.txt

Patterns are regexes matched against the file or folder name, or against the full path when
the pattern contains a path separator. `--exclude-from` reads one per line, ignoring blank
lines and `#` comments.

> **Careful:** adding any exclusion replaces the built-in "skip folders whose name starts with
> a dot" fallback, so `--exclude '^node_modules$'` on its own will start descending into
> `.git`. Pass `--exclude-defaults` alongside it to keep that behaviour — it applies the same
> set as the GUI's Restore Defaults button (OS metadata, trash folders, dot-prefixed names).

An ignore list saved by the GUI can be loaded too; pairs recorded in it are never reported as
matching each other:

    $ dupeguru-scan ~/Photos --ignore-list ~/.local/share/dupeguru/ignore_list.xml

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Completed, no duplicates found (or nothing deleted) |
| 1 | Completed, duplicates found (or files deleted) |
| 2 | Bad arguments or startup error |
| 3 | Scan failed, or errors were encountered during deletion |

### Deletion

Deletion from the CLI requires an explicit `--yes`; `--delete` alone will refuse to run.

    $ dupeguru-scan /data --delete --yes

`--delete` sends files to the system trash. `--direct-delete` permanently removes them instead.
Files are re-validated against their recorded size and modification time immediately before
removal, and anything that changed since the scan is skipped and reported.

Add `--dry-run` to see what would be removed without removing it. It takes precedence over
`--delete`, does not require `--yes`, and still emits the normal results on stdout:

    $ dupeguru-scan /data --delete --yes --dry-run
    DRY RUN: no files have been deleted.
      would send to trash 412 file(s) in 198 group(s), reclaiming 3.71 GB
      412 matched on full content
      re-run without --dry-run to execute.

### Planning a deletion

`--plan` answers a different question from the results: not "what matched" but "what would
actually be removed, and what would not". It implies no mutation, needs no `--delete`, and
writes a per-file plan as JSON to stdout (or `--output`) in place of the normal results:

    $ dupeguru-scan /data --plan
    DELETION PLAN: no files have been deleted.
      would send to trash 3,881 file(s) in 1,284 group(s), reclaiming 41.2 GB
      3,860 matched on full content
      21 matched on a partial (sampled) hash only and would be refused without --allow-partial-matches
      4 would be skipped: file changed since last scan
      512.00 MB would not be reclaimed because of those skips
      2 are on a different volume from their reference (hardlink replacement would fail)
      nothing has been deleted. Re-run with --delete --yes to execute.

Every candidate carries a verdict:

```json
{"path": "/data/b.txt", "size": 9, "mtime": 1785821371.97,
 "would_delete": false, "match_confidence": "full",
 "blocked_reason": "file changed since last scan"}
```

`match_confidence` is `"full"` or `"partial"` — see [partial matches](#partial-sampled-matches).
`blocked_reason` appears only when `would_delete` is false.

The plan is not an estimate. Each file is re-validated with the same predicate the deletion
itself uses, so a file that changed since the scan is reported as skipped rather than counted
as reclaimable — and the set of paths the plan says will go is exactly the set the subsequent
`--delete` removes. `--plan` works against a saved results file too, which is where it earns
its keep: results from last week may describe a directory that has moved on.

    $ dupeguru-scan --from-results results.json --plan

If any marked file was matched on a partial (sampled) hash rather than full content — only
possible when `--partial-hash-threshold` is in use — `--delete` refuses and exits 2. Those are
probable duplicates, not confirmed ones. Pass `--allow-partial-matches` to delete them anyway,
or drop `--partial-hash-threshold` to compare full contents. The same refusal applies when
deleting from a saved results file with `--from-results`.

### Partial (sampled) matches

`--partial-hash-threshold` speeds up scans by comparing three sampled chunks of a large file
instead of its full contents. That is a real trade: two different files can agree on every
sampled chunk. Because such a pair still scores 100%, the match percentage alone cannot tell
you which results are certain, so every duplicate entry carries a `partial_match` flag and
each stats record carries a `partial_matches` count:

    $ dupeguru-scan /data --partial-hash-threshold 64 | jq '.stats.partial_matches'
    21

To resolve them rather than just see them, `--full-verify` re-reads only the files involved in
partial matches, compares them in full, and discards any pair that turns out not to match:

    $ dupeguru-scan /data --partial-hash-threshold 64 --full-verify
    Full verification: 20 partial match(es) confirmed on full content, 1 discarded as false positive(s).

Verified matches are no longer partial, so `--delete` accepts them without
`--allow-partial-matches`. The cost is one extra read of just those files, which is far less
than scanning without a threshold at all.

Results saved before this fork recorded `partial_match` cannot be checked. Deleting from such
a file warns and proceeds rather than reporting a false zero; re-scan if you need the flag.

Run `dupeguru-scan --help` for the full option list, including the scanner knobs
(`--min-match`, `--min-size`, `--max-size`, `--partial-hash-threshold`, and others).

## Running tests

The complete test suite is run with [Tox 1.7+][tox]. If you have it installed system-wide, you
don't even need to set up a virtualenv. Just `cd` into the root project folder and run `tox`.

If you don't have Tox system-wide, install it in your virtualenv with `pip install tox` and then
run `tox`.

You can also run automated tests without Tox. Extra requirements for running tests are in
`requirements-extra.txt`. So, you can do `pip install -r requirements-extra.txt` inside your
virtualenv and then `py.test core hscommon`

### Coverage

    $ pytest core hscommon --cov=core --cov=hscommon --cov=cli --cov-report=term-missing

CI runs the same command and uploads `coverage.xml` as a build artifact.

### Linting

`black` and `flake8` are enforced in CI through pre-commit. Install the hooks locally so they
run before each commit:

    $ pip install pre-commit && pre-commit install

[dupeguru]: https://dupeguru.voltaicideas.net/
[upstream]: https://github.com/arsenetar/dupeguru
[fork]: https://github.com/haggyroth/dupeguru
[fork-issues]: https://github.com/haggyroth/dupeguru/issues
[cross-toolkit]: http://www.hardcoded.net/articles/cross-toolkit-software
[documentation]: http://dupeguru.voltaicideas.net/help/en/
[python]: http://www.python.org/
[pyqt]: http://www.riverbankcomputing.com
[tox]: https://tox.readthedocs.org/en/latest/
