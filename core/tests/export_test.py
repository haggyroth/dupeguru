# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Tests for core.export, which previously had none (26% covered)."""

import csv
import os.path as op

from pytest import raises
from hscommon.testutil import eq_

from core.export import export_to_csv, export_to_xhtml


COLNAMES = ["Filename", "Folder", "Size (KB)"]
# Rows are [group_id, filename, *cells] -- the group id doubles as the indent flag source.
ROWS = [
    [0, "ref.txt", "/a", "10"],
    [0, "dupe.txt", "/a", "10"],
    [1, "other.txt", "/b", "20"],
]


# ---------------------------------------------------------------------------
# XHTML
# ---------------------------------------------------------------------------


def test_xhtml_writes_a_file_that_contains_every_value():
    path = export_to_xhtml(COLNAMES, ROWS)
    assert op.exists(path)
    content = open(path, encoding="utf-8").read()
    for name in COLNAMES:
        assert f"<th>{name}</th>" in content
    for row in ROWS:
        for value in row[1:]:
            assert value in content


def test_xhtml_indents_only_non_reference_rows():
    """The first row of each group is the keeper and must not be indented."""
    path = export_to_xhtml(COLNAMES, ROWS)
    content = open(path, encoding="utf-8").read()
    # Two group changes (0 then 1) produce two unindented rows; one repeat produces one indented.
    eq_(content.count('class="indented"'), 1)
    eq_(content.count('class=""'), 2)


def test_xhtml_handles_no_rows():
    path = export_to_xhtml(COLNAMES, [])
    content = open(path, encoding="utf-8").read()
    assert "<h1>dupeGuru Results</h1>" in content
    assert "<td class=" not in content


def test_xhtml_rejects_rows_of_the_wrong_width():
    with raises(AssertionError):
        export_to_xhtml(COLNAMES, [[0, "only-a-filename"]])


def test_xhtml_writes_utf8():
    path = export_to_xhtml(COLNAMES, [[0, "café – naïve.txt", "/a", "1"]])
    content = open(path, encoding="utf-8").read()
    assert "café – naïve.txt" in content


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_writes_header_and_rows(tmpdir):
    dest = str(tmpdir.join("out.csv"))
    export_to_csv(dest, COLNAMES, ROWS)
    with open(dest, encoding="utf-8", newline="") as fp:
        parsed = list(csv.reader(fp))
    eq_(parsed[0], ["Group ID"] + COLNAMES)
    eq_(len(parsed), len(ROWS) + 1)
    eq_(parsed[1], ["0", "ref.txt", "/a", "10"])


def test_csv_handles_no_rows(tmpdir):
    dest = str(tmpdir.join("empty.csv"))
    export_to_csv(dest, COLNAMES, [])
    with open(dest, encoding="utf-8", newline="") as fp:
        parsed = list(csv.reader(fp))
    eq_(parsed, [["Group ID"] + COLNAMES])


def test_csv_quotes_values_containing_separators(tmpdir):
    dest = str(tmpdir.join("commas.csv"))
    export_to_csv(dest, COLNAMES, [[0, 'has,comma and "quote".txt', "/a", "1"]])
    with open(dest, encoding="utf-8", newline="") as fp:
        parsed = list(csv.reader(fp))
    eq_(parsed[1][1], 'has,comma and "quote".txt')


def test_csv_writes_utf8(tmpdir):
    dest = str(tmpdir.join("utf8.csv"))
    export_to_csv(dest, COLNAMES, [[0, "café.txt", "/a", "1"]])
    with open(dest, encoding="utf-8", newline="") as fp:
        parsed = list(csv.reader(fp))
    eq_(parsed[1][1], "café.txt")


def test_csv_raises_on_an_unwritable_destination(tmpdir):
    """core.app catches OSError here to show "Couldn't write to file"; it must actually raise."""
    with raises(OSError):
        export_to_csv(str(tmpdir), COLNAMES, ROWS)  # a directory, not a file
