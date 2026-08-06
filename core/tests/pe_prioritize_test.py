# Copyright 2026 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

from core.pe.prioritize import ExifTimestampCategory, all_categories, parse_exif_timestamp
from core.tests.base import eq_


class FakePhoto:
    """Just enough of a Photo for a prioritization category to read."""

    def __init__(self, name, exif_timestamp=""):
        self.name = name
        self.exif_timestamp = exif_timestamp


def sorted_names(photos, crit_value):
    category = ExifTimestampCategory(results=None)
    return [p.name for p in sorted(photos, key=lambda p: category.sort_key(p, crit_value))]


# --- parse_exif_timestamp


def test_parse_exif_timestamp_reads_the_standard_format():
    # EXIF DateTimeOriginal is "YYYY:MM:DD HH:MM:SS".
    eq_(parse_exif_timestamp("2018:03:04 05:06:07"), 20180304050607)


def test_parse_exif_timestamp_orders_chronologically():
    # A later capture must always parse to a larger number, including across
    # the boundaries where a naive string comparison is easy to get wrong.
    ordered = [
        "1999:12:31 23:59:59",
        "2000:01:01 00:00:00",
        "2018:03:04 05:06:07",
        "2018:03:04 05:06:08",
        "2018:12:04 05:06:07",
        "2019:01:01 00:00:00",
    ]
    parsed = [parse_exif_timestamp(value) for value in ordered]
    eq_(parsed, sorted(parsed))


def test_parse_exif_timestamp_accepts_an_iso_style_separator():
    # Some editors write "T" between the date and the time.
    eq_(parse_exif_timestamp("2018:03:04T05:06:07"), 20180304050607)


def test_parse_exif_timestamp_returns_none_when_there_is_no_date():
    # "" is the initial value on Photo, so this is the common case.
    eq_(parse_exif_timestamp(""), None)
    eq_(parse_exif_timestamp(None), None)
    eq_(parse_exif_timestamp("not a date"), None)
    eq_(parse_exif_timestamp("2018:03:04"), None)  # date only, no time


def test_parse_exif_timestamp_rejects_the_all_zero_placeholder():
    # Written by devices whose clock was never set; it is not 'the oldest photo'.
    eq_(parse_exif_timestamp("0000:00:00 00:00:00"), None)


# --- ExifTimestampCategory


def test_exif_timestamp_category_is_offered_in_picture_mode():
    assert ExifTimestampCategory in all_categories()


def test_exif_timestamp_criterion_is_labelled_by_age():
    category = ExifTimestampCategory(results=None)
    eq_(category.format_criterion_value(category.HIGHEST), "Newest")
    eq_(category.format_criterion_value(category.LOWEST), "Oldest")


def test_oldest_capture_sorts_first():
    photos = [
        FakePhoto("newer", "2020:06:01 12:00:00"),
        FakePhoto("older", "2010:06:01 12:00:00"),
        FakePhoto("middle", "2015:06:01 12:00:00"),
    ]
    category = ExifTimestampCategory(results=None)
    eq_(sorted_names(photos, category.LOWEST), ["older", "middle", "newer"])


def test_newest_capture_sorts_first():
    photos = [
        FakePhoto("newer", "2020:06:01 12:00:00"),
        FakePhoto("older", "2010:06:01 12:00:00"),
        FakePhoto("middle", "2015:06:01 12:00:00"),
    ]
    category = ExifTimestampCategory(results=None)
    eq_(sorted_names(photos, category.HIGHEST), ["newer", "middle", "older"])


def test_photos_without_a_capture_date_sort_last_in_both_directions():
    # This is the whole point of the missing-value rule: a screenshot with no
    # EXIF must not win "Oldest" just because "" compares low.
    photos = [
        FakePhoto("screenshot"),
        FakePhoto("shot-2010", "2010:06:01 12:00:00"),
        FakePhoto("shot-2020", "2020:06:01 12:00:00"),
    ]
    category = ExifTimestampCategory(results=None)
    eq_(sorted_names(photos, category.LOWEST), ["shot-2010", "shot-2020", "screenshot"])
    eq_(sorted_names(photos, category.HIGHEST), ["shot-2020", "shot-2010", "screenshot"])


def test_a_copy_keeps_its_capture_date_even_though_its_mtime_moved():
    # The motivating case. Both files are the same photo; the copy was written
    # later, so mtime would prefer it, but the capture date is identical and the
    # ordering must not invent a difference.
    original = FakePhoto("original", "2015:06:01 12:00:00")
    copy = FakePhoto("copy", "2015:06:01 12:00:00")
    category = ExifTimestampCategory(results=None)
    eq_(category.sort_key(original, category.LOWEST), category.sort_key(copy, category.LOWEST))
