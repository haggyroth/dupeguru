# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Detect compiled C extensions that are older than the sources they were built from.

Pulling does not rebuild the extensions in ``setup.py``, and nothing warns that they are out
of date. The resulting failure is uniquely bad at explaining itself: a stale ``_block`` handed
a ``bytes`` object by a test written for the newer ``avgdiff`` reads it as a sequence of tuples
and walks off the end of memory, so pytest dies with a Windows access violation instead of a
test failure -- no assertion text, no report, and the crash lands inside a legitimate test of
recently changed code (issue #176).

It is also per-machine: it reproduces only for whoever last built before the source changed, so
one developer sees a hard crash in the picture matcher and another sees a clean run. CI never
sees it at all, because CI builds from clean every time -- which is exactly why this has to be
caught locally or not at all.

The extension-to-source mapping is read out of ``setup.py`` rather than repeated here, so a new
extension or a new source file cannot leave this check silently covering less than it claims to.
``setup.py`` is parsed, not imported, because importing it runs ``setup()``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def extension_sources(setup_py: Path) -> dict[str, list[Path]]:
    """Map each ``Extension`` name in *setup_py* to its source files.

    Returns paths relative to the repository root, in the order ``setup.py`` lists them.
    """
    tree = ast.parse(setup_py.read_text(encoding="utf-8"))
    extensions: dict[str, list[Path]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Extension"
        ):
            continue
        # Extension(name, sources, ...) -- both are positional in setup.py.
        if len(node.args) < 2:
            continue
        name = _literal_str(node.args[0])
        sources = _literal_path_list(node.args[1])
        if name is not None and sources:
            extensions[name] = sources
    return extensions


def _literal_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_path_list(node: ast.expr) -> list[Path]:
    """Read a list of ``str(Path("a", "b"))`` calls, the form setup.py uses."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    paths = []
    for element in node.elts:
        parts = _path_parts(element)
        if parts:
            paths.append(Path(*parts))
    return paths


def _path_parts(node: ast.expr) -> list[str]:
    # str(Path("core", "pe", "modules", "block.c"))
    if (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "str"
        and node.args
    ):
        node = node.args[0]
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Path":
        parts = [_literal_str(a) for a in node.args]
        return (
            [p for p in parts if p is not None]
            if all(p is not None for p in parts)
            else []
        )
    literal = _literal_str(node)
    return [literal] if literal is not None else []


def built_extension(module_name: str, repo_root: Path) -> Path | None:
    """Find the built artifact for ``core.pe._block``-style *module_name*, if any.

    The suffix carries the interpreter and platform (``.cp314-win_amd64.pyd``), so this globs
    rather than reconstructing it -- a build for a different Python is still a build, and
    still stale.
    """
    *package, stem = module_name.split(".")
    directory = repo_root.joinpath(*package)
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob(f"{stem}.*.pyd")) + sorted(
        directory.glob(f"{stem}.*.so")
    )
    # An unsuffixed artifact is possible on some toolchains.
    candidates += sorted(directory.glob(f"{stem}.pyd")) + sorted(
        directory.glob(f"{stem}.so")
    )
    return candidates[0] if candidates else None


def stale_extensions(repo_root: Path | None = None) -> list[tuple[Path, Path]]:
    """Return ``(built artifact, newer source)`` for every extension that needs rebuilding.

    An extension that was never built is not stale -- the tests that need it skip on
    ``ImportError``, which reports itself perfectly well. Only a build that exists *and*
    predates its source is the silent case worth warning about.
    """
    repo_root = repo_root or REPO_ROOT
    setup_py = repo_root / "setup.py"
    if not setup_py.is_file():
        return []

    stale = []
    for module_name, sources in extension_sources(setup_py).items():
        artifact = built_extension(module_name, repo_root)
        if artifact is None:
            continue
        built_at = artifact.stat().st_mtime
        newer = [
            repo_root / s
            for s in sources
            if (repo_root / s).is_file() and (repo_root / s).stat().st_mtime > built_at
        ]
        if newer:
            # Report the most recently touched source; it is the one that explains the crash.
            stale.append((artifact, max(newer, key=lambda p: p.stat().st_mtime)))
    return stale


def stale_report(repo_root: Path | None = None) -> str | None:
    """A human-readable warning, or None when every built extension is current."""
    stale = stale_extensions(repo_root)
    if not stale:
        return None
    root = repo_root or REPO_ROOT
    lines = ["Compiled C extensions are older than their sources:"]
    for artifact, source in stale:
        lines.append(f"  {artifact.relative_to(root)}  <  {source.relative_to(root)}")
    lines.append("")
    lines.append("Rebuild them with:  python build.py --modules")
    lines.append(
        "Until then a picture-matching test may crash the interpreter rather than fail."
    )
    return "\n".join(lines)
