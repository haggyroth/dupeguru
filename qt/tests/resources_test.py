# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The committed resource module must match the images it was generated from.

``qt/resources_data.py`` is generated but committed, so that there is no resource build step
left to fail silently -- the failure mode that produced an icon-less GUI from a missing
pyrcc5. The cost of committing generated output is that it can drift from its source; these
tests remove that risk by regenerating and comparing.

These need no Qt bindings, so unlike the rest of qt/tests/ they also run on the Linux legs.
"""

import base64
import sys
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[2]
QRC = REPO_ROOT / "qt" / "dg.qrc"


def _build_module():
    """Import build.py from the repo root without requiring it to be on sys.path."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import build

        return build
    finally:
        sys.path.pop(0)


def test_committed_resources_match_the_images():
    """Fails if an image changed without `python build.py --resources` being re-run."""
    from qt.resources_data import RESOURCES

    manifest = _build_module().read_resource_manifest()
    assert set(RESOURCES) == set(manifest), "resources_data.py and dg.qrc declare different names"

    stale = []
    for alias, path in manifest.items():
        if base64.b64decode(RESOURCES[alias]) != path.read_bytes():
            stale.append(alias)
    assert not stale, f"stale resources, re-run `python build.py --resources`: {stale}"


def test_generator_output_is_reproducible():
    """The committed file should be exactly what the generator emits, byte for byte."""
    build = _build_module()
    committed = (REPO_ROOT / "qt" / "resources_data.py").read_text(encoding="utf-8")
    assert build.render_resources_module() == committed, "run `python build.py --resources`"


def test_manifest_references_existing_files():
    manifest = _build_module().read_resource_manifest()
    assert manifest, "dg.qrc declares no resources"
    missing = [str(p) for p in manifest.values() if not p.exists()]
    assert not missing, f"dg.qrc references files that do not exist: {missing}"


def test_qrc_aliases_are_unique():
    """Duplicate aliases would silently drop one image from the generated dict."""
    aliases = [e.get("alias") for e in ElementTree.parse(QRC).iter("file")]
    assert len(aliases) == len(set(aliases)), f"duplicate aliases in dg.qrc: {aliases}"
