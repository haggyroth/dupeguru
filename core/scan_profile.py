# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Named scan configurations: folders, reference folders, mode, and settings, saved together.

Re-running the same scan otherwise means reconstructing it by hand every time. The individual
pieces are already persisted -- ``last_directories.xml`` holds the folders and their states,
preferences hold the rest -- but only as "the last one", and never as a set that belongs
together. This groups and names them.

Settings are kept opaque here on purpose. Core has no business knowing that a front end
expresses a size threshold as a kilobyte figure plus an "ignore small files" checkbox; it
stores whatever scalars it is handed and gives them back unchanged. That keeps the round trip
exact, where a translation through core's own option names would have to invert a lossy
mapping and would quietly get it wrong.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from hscommon.util import FileOrPath


class ScanProfileError(Exception):
    """A profile could not be saved or applied."""


#: Scalars a setting may hold, and how each survives a round trip through XML. Anything else
#: is refused at capture time rather than silently dropped when the file is read back.
_TYPE_NAMES = {bool: "bool", int: "int", float: "float", str: "str", set: "set"}


def _encode(value):
    """Return ``(type_name, text)`` for a settings value."""
    # Exact type, not isinstance: bool is a subclass of int, and a True stored as an int comes
    # back through int("True") as a ValueError. Listing bool first as well means the order and
    # the check would each have to be wrong before that breaks.
    for kind in (bool, int, float, str, set):
        if type(value) is kind:
            if kind is set:
                if not all(type(item) is str for item in value):
                    raise ScanProfileError("only sets of strings can be saved in a profile")
                # Sorted so that saving the same profile twice produces the same file, which
                # matters if anyone ever diffs or syncs these.
                return "set", "\n".join(sorted(value))
            return _TYPE_NAMES[kind], str(value)
    raise ScanProfileError(f"cannot save a setting of type {type(value).__name__}")


def _decode(type_name: str, text: str):
    """Rebuild a settings value from its stored form, or raise ScanProfileError."""
    if type_name == "bool":
        return text == "True"
    if type_name == "int":
        return int(text)
    if type_name == "float":
        return float(text)
    if type_name == "str":
        return text
    if type_name == "set":
        # "".split("\n") is [""], which would resurrect an empty set as {""}.
        return set(text.split("\n")) if text else set()
    raise ScanProfileError(f"unknown setting type {type_name!r}")


class ScanProfile:
    """One named scan configuration."""

    def __init__(self, name: str, app_mode: int = 0, folders=None, states=None, settings=None):
        self.name = name
        self.app_mode = app_mode
        #: Root folders to scan, as strings. Order is preserved because the first folder is
        #: what the directory tree opens on.
        self.folders = list(folders or [])
        #: path -> DirectoryState, for folders whose state differs from the default. Reference
        #: folders live here, and losing them would turn protected originals into candidates
        #: for deletion.
        self.states = dict(states or {})
        #: Front-end settings, stored and returned unchanged.
        self.settings = dict(settings or {})

    def __repr__(self):
        return f"<ScanProfile {self.name!r} {len(self.folders)} folder(s)>"

    def __eq__(self, other):
        if not isinstance(other, ScanProfile):
            return NotImplemented
        return (
            self.name == other.name
            and self.app_mode == other.app_mode
            and self.folders == other.folders
            and self.states == other.states
            and self.settings == other.settings
        )

    @classmethod
    def capture(cls, name: str, directories, app_mode: int, settings=None) -> "ScanProfile":
        """Snapshot the folders and states currently held by *directories*."""
        return cls(
            name=name,
            app_mode=app_mode,
            folders=[str(path) for path in directories],
            states={str(path): int(state) for path, state in directories.states.items()},
            settings=settings,
        )

    def missing_folders(self) -> list:
        """Root folders in this profile that are no longer present, in stored order.

        Checked so a front end can say so. A profile that silently scans four folders because
        the fifth was unplugged reports fewer duplicates than the user is expecting, and the
        absence of a result reads exactly like a clean result.
        """
        return [folder for folder in self.folders if not Path(folder).exists()]

    def apply_folders(self, directories) -> list:
        """Replace *directories*' contents with this profile's. Returns the missing folders.

        Missing folders are skipped rather than refused: with an external drive unplugged, the
        useful behaviour is to scan what is there and be told what is not.
        """
        from core.directories import AlreadyThereError, InvalidPathError

        missing = []
        directories.clear()
        for folder in self.folders:
            path = Path(folder)
            if not path.exists():
                missing.append(folder)
                continue
            try:
                directories.add_path(path)
            except (AlreadyThereError, InvalidPathError):
                # add_path() raises AlreadyThereError for a subfolder of one already added.
                # Not an error to report: the folder is still covered by the scan.
                pass
        for folder, state in self.states.items():
            path = Path(folder)
            # A state for a folder that is gone would sit in the dict forever, and states are
            # matched by prefix -- a stale entry can capture a later folder at the same path.
            if path.exists():
                directories.states[path] = state
        return missing


class ProfileStore:
    """The saved profiles, keyed by name.

    Names are the identity, so saving under an existing name replaces it. That is what makes
    "save the current setup as My Photos" work twice without accumulating duplicates.
    """

    def __init__(self):
        self._profiles = {}

    def __len__(self):
        return len(self._profiles)

    def __contains__(self, name):
        return name in self._profiles

    def __iter__(self):
        """Profiles in name order, so a front end listing them needs no sort of its own."""
        return iter(sorted(self._profiles.values(), key=lambda p: p.name.lower()))

    @property
    def names(self) -> list:
        return [profile.name for profile in self]

    def get(self, name):
        return self._profiles.get(name)

    def set(self, profile: ScanProfile) -> None:
        if not profile.name or not profile.name.strip():
            raise ScanProfileError("a profile needs a name")
        self._profiles[profile.name] = profile

    def remove(self, name: str) -> None:
        self._profiles.pop(name, None)

    def clear(self) -> None:
        self._profiles.clear()

    # --- Persistence

    def load_from_file(self, infile) -> None:
        """Read profiles from XML written by :meth:`save_to_file`.

        A file that cannot be parsed leaves the store empty rather than raising. This is read
        during startup, and a corrupt profile file is not a reason to refuse to launch --
        matching how Directories.load_from_file already treats last_directories.xml.
        """
        try:
            root = ET.parse(infile).getroot()
        except Exception:
            return
        for node in root.iter("profile"):
            name = node.get("name")
            if not name:
                continue
            try:
                app_mode = int(node.get("app_mode", "0"))
            except ValueError:
                app_mode = 0
            folders = [fn.get("path") for fn in node.iter("folder") if fn.get("path")]
            states = {}
            for sn in node.iter("state"):
                path, value = sn.get("path"), sn.get("value")
                if path is None or value is None:
                    continue
                try:
                    states[path] = int(value)
                except ValueError:
                    continue
            settings = {}
            for setting in node.iter("setting"):
                key, type_name = setting.get("key"), setting.get("type")
                if key is None or type_name is None:
                    continue
                try:
                    settings[key] = _decode(type_name, setting.get("value") or "")
                except (ScanProfileError, ValueError):
                    # One unreadable setting must not cost the whole profile: the folders and
                    # the rest of the configuration are still worth having.
                    continue
            self._profiles[name] = ScanProfile(name, app_mode, folders, states, settings)

    def save_to_file(self, outfile) -> None:
        """Write every profile as XML to *outfile*."""
        with FileOrPath(outfile, "wb") as fp:
            root = ET.Element("scan_profiles")
            for profile in self:
                node = ET.SubElement(root, "profile")
                node.set("name", profile.name)
                node.set("app_mode", str(profile.app_mode))
                for folder in profile.folders:
                    ET.SubElement(node, "folder").set("path", folder)
                for path, state in sorted(profile.states.items()):
                    state_node = ET.SubElement(node, "state")
                    state_node.set("path", path)
                    state_node.set("value", str(state))
                for key, value in sorted(profile.settings.items()):
                    type_name, text = _encode(value)
                    setting_node = ET.SubElement(node, "setting")
                    setting_node.set("key", key)
                    setting_node.set("type", type_name)
                    setting_node.set("value", text)
            ET.ElementTree(root).write(fp, encoding="utf-8")
