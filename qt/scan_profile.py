# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Which preferences belong to a scan profile, and how to move them in and out (issue #133).

Profiles store *preference* values rather than the option dict core scans with, because
``DupeGuru._update_options`` rebuilds that dict from preferences whenever they are applied. A
profile written straight into ``app.model.options`` would be silently overwritten the next time
the user opened the preferences dialog, and the dialog would meanwhile show settings that did
not match the scan about to run.

Going through preferences also avoids inverting a lossy mapping: ``size_threshold`` is a
kilobyte figure multiplied out, but zero means "ignore small files is switched off" rather than
"the threshold is zero", and reconstructing the checkbox from the product is guesswork.
"""

from core.app import AppMode
from core.directories import DirectoryState

#: Preferences that describe what a scan does. Everything about appearance, language, window
#: layout and result presentation is deliberately absent: restoring a saved scan should not
#: change the theme, and a profile that did would be a surprise nobody asked for.
#:
#: ``destination_type`` is likewise out. It governs where copied or moved files land, which is
#: something you do to results afterwards, not part of finding them.
SCAN_PREFERENCES = [
    "filter_hardness",
    "mix_file_kind",
    "use_regexp",
    "ignore_hardlink_matches",
    "remove_empty_folders",
    "rehash_ignore_mtime",
    "include_exists_check",
    "word_weighting",
    "match_similar",
    "ignore_small_files",
    "small_file_threshold",
    "ignore_large_files",
    "large_file_threshold",
    "big_file_partial_hashes",
    "big_file_size_threshold",
    "full_verify",
    "cache_file_list",
    "combine_picture_matching",
    "scan_tag_track",
    "scan_tag_artist",
    "scan_tag_album",
    "scan_tag_title",
    "scan_tag_genre",
    "scan_tag_year",
    "match_scaled",
    "match_rotated",
]

#: The scan type is stored per mode rather than as a plain attribute, so it travels under its
#: own key instead of coming along with the list above.
SCAN_TYPE_KEY = "scan_type"


def capture_settings(prefs, app_mode: int) -> dict:
    """Read the profile-relevant preferences into a flat dict of scalars."""
    settings = {name: getattr(prefs, name) for name in SCAN_PREFERENCES if hasattr(prefs, name)}
    settings[SCAN_TYPE_KEY] = prefs.get_scan_type(app_mode)
    return settings


def apply_settings(prefs, settings: dict, app_mode: int) -> None:
    """Write a profile's settings back into *prefs*.

    Unknown keys are ignored, so a profile written by a later version that knows about a
    preference this one does not still restores everything else. The alternative -- refusing
    the whole profile over one unrecognised key -- would lose the folders too.
    """
    for name in SCAN_PREFERENCES:
        if name in settings and hasattr(prefs, name):
            setattr(prefs, name, settings[name])
    if SCAN_TYPE_KEY in settings:
        # Set against the profile's own mode, not whatever mode is showing: a picture profile
        # carries a picture scan type, and writing it into the standard-mode slot would leave
        # both modes wrong.
        prefs.set_scan_type(app_mode, settings[SCAN_TYPE_KEY])


def describe(profile) -> str:
    """A one-line summary of a profile, for a list the user picks from."""
    mode_names = {AppMode.STANDARD: "Standard", AppMode.MUSIC: "Music", AppMode.PICTURE: "Picture"}
    mode = mode_names.get(profile.app_mode, "Standard")
    count = len(profile.folders)
    folders = "1 folder" if count == 1 else f"{count} folders"
    reference_count = sum(1 for state in profile.states.values() if state == DirectoryState.REFERENCE)
    if reference_count:
        folders += f", {reference_count} reference"
    return f"{mode} — {folders}"
