# Contributing to dupeGuru

The following is a set of guidelines and information for contributing to dupeGuru.

> **This repository is a fork.** It is maintained at
> [haggyroth/dupeguru](https://github.com/haggyroth/dupeguru) and is not intended to feed changes
> back to [arsenetar/dupeguru](https://github.com/arsenetar/dupeguru). File all issues and pull
> requests against this fork. Do not open issues, pull requests, or discussions on the upstream
> repository.

#### Table of Contents

[Things to Know Before Starting](#things-to-know-before-starting)

[Ways to Contribute](#ways-to-contribute)
  * [Reporting Bugs](#reporting-bugs)
  * [Suggesting Enhancements](#suggesting-enhancements)
  * [Localization](#localization)
  * [Code Contribution](#code-contribution)
  * [Pull Requests](#pull-requests)

[Style Guides](#style-guides)
  * [Git Commit Messages](#git-commit-messages)
  * [Python Style Guide](#python-style-guide)
  * [Documentation Style Guide](#documentation-style-guide)

[Additional Notes](#additional-notes)
  * [Issue and Pull Request Labels](#issue-and-pull-request-labels)

## Things to Know Before Starting
**TODO**
## Ways to contribute
### Reporting Bugs
**TODO**
### Suggesting Enhancements
**TODO**
### Localization
**TODO**
### Code Contribution
**TODO**
### Pull Requests
Please follow these steps to have your contribution considered by the maintainers:

1. Keep Pull Request specific to one feature or bug.
2. Follow the [style guides](#style-guides)
3. After you submit your pull request, verify that all [status checks](https://help.github.com/articles/about-status-checks/) are passing <details><summary>What if the status checks are failing?</summary>If a status check is failing, and you believe that the failure is unrelated to your change, please leave a comment on the pull request explaining why you believe the failure is unrelated. A maintainer will re-run the status check for you. If we conclude that the failure was a false positive, then we will open an issue to track that problem with our status check suite.</details>

While the prerequisites above must be satisfied prior to having your pull request reviewed, the reviewer(s) may ask you to complete additional design work, tests, or other changes before your pull request can be ultimately accepted.

## Style Guides
### Git Commit Messages
- Use the present tense ("Add feature" not "Added feature")
- Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters or less
- Reference issues and pull requests liberally after the first line

### Python Style Guide
- All files are formatted with [Black](https://github.com/psf/black)
- Follow [PEP 8](https://peps.python.org/pep-0008/) as much as practical
- Pass [flake8](https://flake8.pycqa.org/en/latest/) linting
- Include [PEP 484](https://peps.python.org/pep-0484/) type hints (new code)

### Documentation Style Guide
**TODO**

## Cutting a release

1. Bump `__version__` in `core/__init__.py`. `setup.cfg` reads it via `attr:`, so nothing else
   needs editing there.
2. Move the accumulated `[Unreleased]` section in `CHANGELOG.md` under the new version heading
   with today's date, leave a fresh empty `[Unreleased]` above it, and update the compare links
   at the foot of the file.
3. Add an entry to `help/changelog`. This is a separate, older-format file that feeds the
   Sphinx docs version — `hscommon/sphinxgen.py` takes the version from its newest entry, not
   from `core.__version__`. Do **not** use `#123` references there: `build.py` linkifies them
   against the *upstream* issue tracker, so they would resolve to unrelated tickets.
4. Commit, tag `vX.Y.Z`, push both, then create the GitHub release.
5. Dispatch the packaging workflow **on the tag**, not on a branch:
   `gh workflow run packaging.yml --ref vX.Y.Z`. An artifact labelled 4.9.0 that does not
   contain the code tagged 4.9.0 is a mislabelled release, and that has happened once.

   Create the release *before* dispatching, or at least before the build finishes: the
   workflow's `attach` job needs one to exist and fails loudly if it does not.
6. **Confirm the release offers a download.** The `attach` job uploads the installer and disk
   image and then re-reads the release to check they are really there, so this should be a
   formality — but it is the step whose absence shipped 4.19.0 and 4.20.0 with nothing to
   download at all, so confirm it rather than assume:

   ```
   gh release view vX.Y.Z --json assets -q '[.assets[].name]'
   ```

   If you dispatched with `attach: false`, or the job failed after the build succeeded, attach
   by hand instead:

   ```
   gh run download <run-id> --dir /tmp/assets
   gh release upload vX.Y.Z /tmp/assets/*/dupeguru_osx_*.dmg /tmp/assets/*/dupeGuru_win64_*.exe
   ```

> **Release titles must be bare semver.** Name the GitHub release `4.4.1`, not
> `v4.4.1 - some description`. Builds at 4.4.0 and earlier read `release["name"]` and parse it
> as semver, so a descriptive title raises `ValueError` and breaks the update check — and the
> About box — for anyone running them. From 4.4.1 onward the version is read from `tag_name`
> instead, so this constraint can be dropped once no one is running an affected build.

## Additional Notes
### Issue and Pull Request Labels
This section lists and describes the various labels used with issues and pull requests.  Each of the labels is listed with a search link as well.

#### Issue Type and Status
| Label name | Search | Description |
|------------|--------|-------------|
| `enhancement` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aenhancement) | Feature requests and enhancements. |
| `bug` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Abug) | Bug reports. |
| `duplicate` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aduplicate) | Issue is a duplicate of existing issue. |
| `needs-reproduction` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aneeds-reproduction) | A bug that has not been able to be reproduced. |
| `needs-information` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aneeds-information) | More information needs to be collected about these problems or feature requests (e.g. steps to reproduce). |
| `blocked` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Ablocked) | Issue blocked by other issues. |
| `beginner` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Abeginner) | Less complex issues for users who want to start contributing. |

#### Category Labels
| Label name | Search | Description |
|------------|--------|-------------|
| `3rd party` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3A%223rd%20party%22)  | Related to a 3rd party dependency. |
| `crash` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Acrash) | Related to crashes (complete, or unhandled). |
| `documentation` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Adocumentation) | Related to any documentation. |
| `linux` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3linux) | Related to running on Linux. |
| `mac` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Amac) | Related to running on macOS. |
| `performance` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aperformance) | Related to the performance. |
| `ui` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Aui)| Related to the visual design. |
| `windows` | [search](https://github.com/haggyroth/dupeguru/issues?q=is%3Aopen+is%3Aissue+label%3Awindows) | Related to running on Windows. |

#### Pull Request Labels
None at this time, if the volume of Pull Requests increase labels may be added to manage.
