# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Every way of running the suite must run the same suite (issue #203).

Four places name the test directories: pytest's ``testpaths``, the Makefile, tox.ini and the
CI workflow. Before ``testpaths`` existed the other three were load-bearing -- a bare
``pytest`` walked the whole tree and aborted during collection, because mutmut leaves a full
copy of core/, qt/ and hscommon/ under mutants/ and two copies of ``core.tests.conftest`` is
an ImportPathMismatchError.

That is fixed by naming the directories once. The risk it introduces is drift: a fifth test
directory added to the Makefile and the workflow but not to ``testpaths`` would be silently
skipped by anyone running a bare ``pytest``, and one added to ``testpaths`` alone would be
skipped by CI. Either way the suite reports success on tests it never ran, which is the
failure this repo's tooling tests exist to catch. So the four are compared against each other
rather than against a list written here.

The settings are read through ``pytestconfig`` rather than by parsing a file, and that is the
point rather than convenience. pytest reads its configuration from the first of pytest.ini,
pyproject.toml, tox.ini and setup.cfg that carries a section -- this repo has both a
pyproject.toml and a setup.cfg, and the settings put in the wrong one are simply ignored, with
no warning and no failure. Asserting against the resolved configuration is the only form of
this test that could notice.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `pytest core hscommon qt tests`, in any of the files that spell it out by hand.
INVOCATION = re.compile(r"(?:py\.test|pytest)((?:\s+[\w./]+)+)")

# Arguments that follow the directory list rather than being part of it.
_NOT_A_DIRECTORY = re.compile(r"^-|^\$|=")


def _named_directories(line: str) -> list:
    match = INVOCATION.search(line)
    if match is None:
        return []
    return [word for word in match.group(1).split() if not _NOT_A_DIRECTORY.match(word)]


def _first_invocation(path: Path) -> list:
    for line in path.read_text(encoding="utf-8").splitlines():
        found = _named_directories(line)
        if found:
            return found
    return []


@pytest.fixture(scope="module")
def testpaths(pytestconfig) -> list:
    resolved = list(pytestconfig.getini("testpaths"))
    assert resolved, (
        "testpaths is unset, so a bare `pytest` will walk the whole tree. Check it is in the "
        f"file pytest actually reads: {pytestconfig.inipath}"
    )
    return resolved


def test_testpaths_names_directories_that_exist(testpaths):
    # A renamed directory would otherwise silently drop out of a bare `pytest` run.
    for name in testpaths:
        assert (REPO_ROOT / name).is_dir(), f"testpaths names {name!r}, which is not a directory"


@pytest.mark.parametrize("filename", ["Makefile", "tox.ini", ".github/workflows/default.yml"])
def test_every_hand_written_invocation_agrees_with_testpaths(testpaths, filename):
    """The drift guard. These four lists must stay the same list."""
    named = _first_invocation(REPO_ROOT / filename)
    assert named, f"found no pytest invocation in {filename}"
    assert named == testpaths, f"{filename} runs {named}, but testpaths is {testpaths}"


def test_the_mutation_tree_is_not_collected(pytestconfig, testpaths):
    """The collision that made a bare `pytest` fail in the first place.

    mutants/ is gitignored, so this only ever affects someone who has run the mutation
    script -- which is exactly the person who then wonders why their checkout is broken.
    """
    assert "mutants" in pytestconfig.getini("norecursedirs")
    assert "mutants" not in testpaths


#: pytest's own defaults, which setting norecursedirs replaces rather than extends.
PYTEST_DEFAULT_NORECURSEDIRS = ["*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"]


def test_overriding_norecursedirs_kept_pytest_s_defaults(pytestconfig):
    """Setting norecursedirs replaces the defaults; forgetting one silently re-enables it.

    ``.*`` is the one that matters and the easiest to lose, because it does not look like a
    list of directories. It excludes *every* dot-directory, so enumerating .tox and .git by
    hand -- which is the obvious thing to write -- quietly starts walking .venv, .pytest_cache
    and anything else beginning with a dot.
    """
    configured = set(pytestconfig.getini("norecursedirs"))
    missing = [name for name in PYTEST_DEFAULT_NORECURSEDIRS if name not in configured]
    assert not missing, f"norecursedirs dropped pytest's defaults: {missing}"


def test_the_mutation_tree_is_not_linted():
    """Same tree, same reason. pre-commit passes an explicit file list and never sees it, but
    tox runs a bare flake8, which would lint every generated file under mutants/."""
    import configparser

    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "tox.ini", encoding="utf-8")
    assert "mutants" in parser["flake8"]["exclude"].split(",")
