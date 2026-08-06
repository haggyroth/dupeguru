# Created On: 2011/09/16
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import re

from hscommon.trans import trget, tr

from core.prioritize import (
    KindCategory,
    FolderCategory,
    FilenameCategory,
    NumericalCategory,
    SizeCategory,
    MtimeCategory,
)

coltr = trget("columns")

# EXIF DateTimeOriginal, as Photo._get_exif_timestamp returns it verbatim:
# "YYYY:MM:DD HH:MM:SS". Some cameras and editors write a space-padded or
# ISO-style separator, so accept either between the date and the time.
_EXIF_TIMESTAMP_RE = re.compile(r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def parse_exif_timestamp(value):
    """Turn an EXIF timestamp string into a sortable integer, or None.

    ``None`` means "this photo has no usable capture date": the field is empty
    (the initial value), unparseable, or the all-zero placeholder some devices
    write when the clock was never set. Returning ``None`` rather than a
    sentinel number keeps those photos out of the ordering entirely, so they
    cannot be mistaken for the oldest shot ever taken.
    """
    match = _EXIF_TIMESTAMP_RE.match(value or "")
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(group) for group in match.groups())
    # "0000:00:00 00:00:00" is a well-known placeholder, not a real date.
    if not (year and month and day):
        return None
    return ((((year * 100 + month) * 100 + day) * 100 + hour) * 100 + minute) * 100 + second


class DimensionsCategory(NumericalCategory):
    NAME = coltr("Dimensions")

    def extract_value(self, dupe):
        return dupe.dimensions

    def invert_numerical_value(self, value):
        width, height = value
        return (-width, -height)


class ExifTimestampCategory(NumericalCategory):
    """Prioritize by when the photo was *taken*, not when the file was written.

    Copying, exporting, syncing and restoring from backup all reset mtime, so
    the copy routinely looks newer than the original and ``Modification`` keeps
    the wrong file. The capture date does not move.
    """

    NAME = coltr("EXIF Timestamp")

    def extract_value(self, dupe):
        return dupe.exif_timestamp

    def format_criterion_value(self, value):
        # Same wording as Modification: "Highest/Lowest" means nothing to a
        # reader thinking about dates.
        return tr("Newest") if value == self.HIGHEST else tr("Oldest")

    def sort_key(self, dupe, crit_value):
        value = parse_exif_timestamp(self.extract_value(dupe))
        if value is None:
            # Photos with no capture date sort last under *both* directions.
            # Scans, screenshots and edited exports are exactly the files a
            # user is least likely to want kept as the reference, and sorting
            # them on a sentinel would make them win "Oldest" outright.
            return (1, 0)
        if crit_value == self.HIGHEST:
            value = self.invert_numerical_value(value)
        return (0, value)


def all_categories():
    return [
        KindCategory,
        FolderCategory,
        FilenameCategory,
        SizeCategory,
        DimensionsCategory,
        MtimeCategory,
        ExifTimestampCategory,
    ]
