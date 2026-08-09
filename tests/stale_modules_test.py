# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The staleness check needs its own tests, for the reason tooling_test.py already gives.

A check that silently matches nothing -- because setup.py moved to a form it cannot parse, or
because the artifact glob stopped matching a platform's suffix -- reports "extensions are
current" on work it never did. That is the same failure the check exists to prevent, one level
up, and it would be invisible: a green run looks identical either way.

So these pin both directions. Every test that asserts staleness is paired with one asserting
the same tree is *not* reported stale once the build is newer.
"""

import os

import pytest

from scripts.stale_modules import (
    REPO_ROOT,
    built_extension,
    extension_sources,
    stale_extensions,
    stale_report,
)

SETUP_PY = """\
from setuptools import setup, Extension
from pathlib import Path

exts = [
    Extension(
        "core.pe._block",
        [
            str(Path("core", "pe", "modules", "block.c")),
            str(Path("core", "pe", "modules", "common.c")),
        ],
        include_dirs=[str(Path("core", "pe", "modules"))],
    ),
    Extension("qt.pe._block_qt", [str(Path("qt", "pe", "modules", "block.c"))]),
]

setup(ext_modules=exts)
"""


@pytest.fixture
def fake_repo(tmp_path):
    """A miniature tree with the same shape as the real one."""
    (tmp_path / "setup.py").write_text(SETUP_PY, encoding="utf-8")
    for rel in ("core/pe/modules", "qt/pe/modules"):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / "core/pe/modules/block.c").write_text("/* c */", encoding="utf-8")
    (tmp_path / "core/pe/modules/common.c").write_text("/* c */", encoding="utf-8")
    (tmp_path / "qt/pe/modules/block.c").write_text("/* c */", encoding="utf-8")
    return tmp_path


def _touch(path, when):
    os.utime(path, (when, when))


OLD, NEW = 1_000_000, 2_000_000


def test_sources_are_read_from_setup_py(fake_repo):
    """The mapping is derived, not duplicated, so a new source cannot go unchecked."""
    sources = extension_sources(fake_repo / "setup.py")

    assert set(sources) == {"core.pe._block", "qt.pe._block_qt"}
    assert [str(p).replace("\\", "/") for p in sources["core.pe._block"]] == [
        "core/pe/modules/block.c",
        "core/pe/modules/common.c",
    ]


def test_the_real_setup_py_is_still_parseable():
    """The guard against this check quietly covering nothing.

    If setup.py is ever rewritten in a form the parser does not recognise, every extension
    silently drops out of the mapping and the run reports itself clean.
    """
    sources = extension_sources(REPO_ROOT / "setup.py")

    assert sources, "no extensions parsed out of setup.py -- the check is covering nothing"
    assert "core.pe._block" in sources
    assert all(srcs for srcs in sources.values()), "an extension parsed with no sources"


@pytest.mark.parametrize(
    "suffix",
    [".cp314-win_amd64.pyd", ".cpython-313-x86_64-linux-gnu.so", ".pyd", ".so"],
)
def test_a_build_is_found_whatever_the_platform_suffix(fake_repo, suffix):
    """The suffix carries interpreter and platform; a build for another Python is still a build."""
    artifact = fake_repo / "core" / "pe" / f"_block{suffix}"
    artifact.write_bytes(b"")

    assert built_extension("core.pe._block", fake_repo) == artifact


def test_a_build_older_than_its_source_is_reported(fake_repo):
    artifact = fake_repo / "core" / "pe" / "_block.cp314-win_amd64.pyd"
    artifact.write_bytes(b"")
    _touch(artifact, OLD)
    _touch(fake_repo / "core/pe/modules/common.c", OLD)
    _touch(fake_repo / "core/pe/modules/block.c", NEW)

    stale = stale_extensions(fake_repo)

    assert [a.name for a, _ in stale] == ["_block.cp314-win_amd64.pyd"]
    assert stale[0][1].name == "block.c"


def test_a_build_newer_than_its_sources_is_not_reported(fake_repo):
    """The paired control: the same tree, only the build is current."""
    artifact = fake_repo / "core" / "pe" / "_block.cp314-win_amd64.pyd"
    artifact.write_bytes(b"")
    _touch(fake_repo / "core/pe/modules/block.c", OLD)
    _touch(fake_repo / "core/pe/modules/common.c", OLD)
    _touch(artifact, NEW)

    assert stale_extensions(fake_repo) == []


def test_a_shared_source_makes_every_extension_built_before_it_stale(fake_repo):
    """common.c is compiled into more than one extension, so touching it stales each of them."""
    for name in ("core/pe/_block.cp314-win_amd64.pyd",):
        (fake_repo / name).write_bytes(b"")
        _touch(fake_repo / name, OLD)
    _touch(fake_repo / "core/pe/modules/block.c", OLD)
    _touch(fake_repo / "core/pe/modules/common.c", NEW)

    stale = stale_extensions(fake_repo)

    assert len(stale) == 1
    assert stale[0][1].name == "common.c", "the shared header/source was not compared"


def test_an_unbuilt_extension_is_not_stale(fake_repo):
    """Never built is not the silent case: the import fails and says so."""
    _touch(fake_repo / "core/pe/modules/block.c", NEW)

    assert stale_extensions(fake_repo) == []


def test_the_report_names_the_rebuild_command(fake_repo):
    """The message has to carry the fix; naming the file without it saves nobody the afternoon."""
    artifact = fake_repo / "core" / "pe" / "_block.cp314-win_amd64.pyd"
    artifact.write_bytes(b"")
    _touch(artifact, OLD)
    _touch(fake_repo / "core/pe/modules/block.c", NEW)

    report = stale_report(fake_repo)

    assert "python build.py --modules" in report
    assert "block.c" in report


def test_there_is_no_report_when_nothing_is_stale(fake_repo):
    assert stale_report(fake_repo) is None
