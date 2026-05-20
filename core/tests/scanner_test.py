# Copyright 2016 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

from hscommon.jobprogress import job
from pathlib import Path
from hscommon.testutil import eq_

from core import fs
from core.engine import getwords, Match
from core.ignore import IgnoreList
from core.scanner import Scanner, ScanType, remove_dupe_paths, _apply_digest
from core.me.scanner import ScannerME


# TODO update this to be able to inherit from fs.File
class NamedObject:
    def __init__(self, name="foobar", size=1, path=None):
        if path is None:
            path = Path(name)
        else:
            path = Path(path, name)
        self.name = name
        self.size = size
        self.path = path
        self.words = getwords(name)

    def __repr__(self):
        return "<NamedObject {!r} {!r}>".format(self.name, self.path)

    def exists(self):
        return self.path.exists()


no = NamedObject


@pytest.fixture
def fake_fileexists(request):
    # This is a hack to avoid invalidating all previous tests since the scanner started to test
    # for file existence before doing the match grouping.
    monkeypatch = request.getfixturevalue("monkeypatch")
    monkeypatch.setattr(Path, "exists", lambda _: True)


def test_empty(fake_fileexists):
    s = Scanner()
    r = s.get_dupe_groups([])
    eq_(r, [])


def test_default_settings(fake_fileexists):
    s = Scanner()
    eq_(s.min_match_percentage, 80)
    eq_(s.scan_type, ScanType.FILENAME)
    eq_(s.mix_file_kind, True)
    eq_(s.word_weighting, False)
    eq_(s.match_similar_words, False)
    eq_(s.size_threshold, 0)
    eq_(s.large_size_threshold, 0)
    eq_(s.big_file_size_threshold, 0)


def test_simple_with_default_settings(fake_fileexists):
    s = Scanner()
    f = [no("foo bar", path="p1"), no("foo bar", path="p2"), no("foo bleh")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    g = r[0]
    # 'foo bleh' cannot be in the group because the default min match % is 80
    eq_(len(g), 2)
    assert g.ref in f[:2]
    assert g.dupes[0] in f[:2]


def test_simple_with_lower_min_match(fake_fileexists):
    s = Scanner()
    s.min_match_percentage = 50
    f = [no("foo bar", path="p1"), no("foo bar", path="p2"), no("foo bleh")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    g = r[0]
    eq_(len(g), 3)


def test_trim_all_ref_groups(fake_fileexists):
    # When all files of a group are ref, don't include that group in the results, but also don't
    # count the files from that group as discarded.
    s = Scanner()
    f = [
        no("foo", path="p1"),
        no("foo", path="p2"),
        no("bar", path="p1"),
        no("bar", path="p2"),
    ]
    f[2].is_ref = True
    f[3].is_ref = True
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    eq_(s.discarded_file_count, 0)


def test_prioritize(fake_fileexists):
    s = Scanner()
    f = [
        no("foo", path="p1"),
        no("foo", path="p2"),
        no("bar", path="p1"),
        no("bar", path="p2"),
    ]
    f[1].size = 2
    f[2].size = 3
    f[3].is_ref = True
    r = s.get_dupe_groups(f)
    g1, g2 = r
    assert f[1] in (g1.ref, g2.ref)
    assert f[0] in (g1.dupes[0], g2.dupes[0])
    assert f[3] in (g1.ref, g2.ref)
    assert f[2] in (g1.dupes[0], g2.dupes[0])


def test_content_scan(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    f = [no("foo"), no("bar"), no("bleh")]
    f[0].digest = f[0].digest_partial = f[0].digest_samples = "foobar"
    f[1].digest = f[1].digest_partial = f[1].digest_samples = "foobar"
    f[2].digest = f[2].digest_partial = f[1].digest_samples = "bleh"
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    eq_(len(r[0]), 2)
    eq_(s.discarded_file_count, 0)  # don't count the different digest as discarded!


def test_content_scan_compare_sizes_first(fake_fileexists):
    class MyFile(no):
        @property
        def digest(self):
            raise AssertionError()

    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    f = [MyFile("foo", 1), MyFile("bar", 2)]
    eq_(len(s.get_dupe_groups(f)), 0)


def test_ignore_file_size(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    small_size = 10  # 10KB
    s.size_threshold = 0
    large_size = 100 * 1024 * 1024  # 100MB
    s.large_size_threshold = 0
    f = [
        no("smallignore1", small_size - 1),
        no("smallignore2", small_size - 1),
        no("small1", small_size),
        no("small2", small_size),
        no("large1", large_size),
        no("large2", large_size),
        no("largeignore1", large_size + 1),
        no("largeignore2", large_size + 1),
    ]
    f[0].digest = f[0].digest_partial = f[0].digest_samples = "smallignore"
    f[1].digest = f[1].digest_partial = f[1].digest_samples = "smallignore"
    f[2].digest = f[2].digest_partial = f[2].digest_samples = "small"
    f[3].digest = f[3].digest_partial = f[3].digest_samples = "small"
    f[4].digest = f[4].digest_partial = f[4].digest_samples = "large"
    f[5].digest = f[5].digest_partial = f[5].digest_samples = "large"
    f[6].digest = f[6].digest_partial = f[6].digest_samples = "largeignore"
    f[7].digest = f[7].digest_partial = f[7].digest_samples = "largeignore"

    r = s.get_dupe_groups(f)
    # No ignores
    eq_(len(r), 4)
    # Ignore smaller
    s.size_threshold = small_size
    r = s.get_dupe_groups(f)
    eq_(len(r), 3)
    # Ignore larger
    s.size_threshold = 0
    s.large_size_threshold = large_size
    r = s.get_dupe_groups(f)
    eq_(len(r), 3)
    # Ignore both
    s.size_threshold = small_size
    r = s.get_dupe_groups(f)
    eq_(len(r), 2)


def test_big_file_partial_hashes(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.CONTENTS

    smallsize = 1
    bigsize = 100 * 1024 * 1024  # 100MB
    s.big_file_size_threshold = bigsize

    f = [no("bigfoo", bigsize), no("bigbar", bigsize), no("smallfoo", smallsize), no("smallbar", smallsize)]
    f[0].digest = f[0].digest_partial = f[0].digest_samples = "foobar"
    f[1].digest = f[1].digest_partial = f[1].digest_samples = "foobar"
    f[2].digest = f[2].digest_partial = "bleh"
    f[3].digest = f[3].digest_partial = "bleh"
    r = s.get_dupe_groups(f)
    eq_(len(r), 2)

    # digest_partial is still the same, but the file is actually different
    f[1].digest = f[1].digest_samples = "difffoobar"
    # here we compare the full digests, as the user disabled the optimization
    s.big_file_size_threshold = 0
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)

    # here we should compare the digest_samples, and see they are different
    s.big_file_size_threshold = bigsize
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)


def test_min_match_perc_doesnt_matter_for_content_scan(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    f = [no("foo"), no("bar"), no("bleh")]
    f[0].digest = f[0].digest_partial = f[0].digest_samples = "foobar"
    f[1].digest = f[1].digest_partial = f[1].digest_samples = "foobar"
    f[2].digest = f[2].digest_partial = f[2].digest_samples = "bleh"
    s.min_match_percentage = 101
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    eq_(len(r[0]), 2)
    s.min_match_percentage = 0
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    eq_(len(r[0]), 2)


def test_content_scan_doesnt_put_digest_in_words_at_the_end(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    f = [no("foo"), no("bar")]
    f[0].digest = f[0].digest_partial = f[0].digest_samples = (
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
    )
    f[1].digest = f[1].digest_partial = f[1].digest_samples = (
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
    )
    r = s.get_dupe_groups(f)
    # FIXME looks like we are missing something here?
    r[0]


def test_extension_is_not_counted_in_filename_scan(fake_fileexists):
    s = Scanner()
    s.min_match_percentage = 100
    f = [no("foo.bar"), no("foo.bleh")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    eq_(len(r[0]), 2)


def test_job(fake_fileexists):
    def do_progress(progress, desc=""):
        log.append(progress)
        return True

    s = Scanner()
    log = []
    f = [no("foo bar"), no("foo bar"), no("foo bleh")]
    s.get_dupe_groups(f, j=job.Job(1, do_progress))
    eq_(log[0], 0)
    eq_(log[-1], 100)


def test_mix_file_kind(fake_fileexists):
    s = Scanner()
    s.mix_file_kind = False
    f = [no("foo.1"), no("foo.2")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 0)


def test_word_weighting(fake_fileexists):
    s = Scanner()
    s.min_match_percentage = 75
    s.word_weighting = True
    f = [no("foo bar"), no("foo bar bleh")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)
    g = r[0]
    m = g.get_match_of(g.dupes[0])
    eq_(m.percentage, 75)  # 16 letters, 12 matching


def test_similar_words(fake_fileexists):
    s = Scanner()
    s.match_similar_words = True
    f = [
        no("The White Stripes"),
        no("The Whites Stripe"),
        no("Limp Bizkit"),
        no("Limp Bizkitt"),
    ]
    r = s.get_dupe_groups(f)
    eq_(len(r), 2)


def test_fields(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.FIELDS
    f = [no("The White Stripes - Little Ghost"), no("The White Stripes - Little Acorn")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 0)


def test_fields_no_order(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.FIELDSNOORDER
    f = [no("The White Stripes - Little Ghost"), no("Little Ghost - The White Stripes")]
    r = s.get_dupe_groups(f)
    eq_(len(r), 1)


def test_tag_scan(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    o1 = no("foo")
    o2 = no("bar")
    o1.artist = "The White Stripes"
    o1.title = "The Air Near My Fingers"
    o2.artist = "The White Stripes"
    o2.title = "The Air Near My Fingers"
    r = s.get_dupe_groups([o1, o2])
    eq_(len(r), 1)


def test_tag_with_album_scan(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"artist", "album", "title"}
    o1 = no("foo")
    o2 = no("bar")
    o3 = no("bleh")
    o1.artist = "The White Stripes"
    o1.title = "The Air Near My Fingers"
    o1.album = "Elephant"
    o2.artist = "The White Stripes"
    o2.title = "The Air Near My Fingers"
    o2.album = "Elephant"
    o3.artist = "The White Stripes"
    o3.title = "The Air Near My Fingers"
    o3.album = "foobar"
    r = s.get_dupe_groups([o1, o2, o3])
    eq_(len(r), 1)


def test_that_dash_in_tags_dont_create_new_fields(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"artist", "album", "title"}
    s.min_match_percentage = 50
    o1 = no("foo")
    o2 = no("bar")
    o1.artist = "The White Stripes - a"
    o1.title = "The Air Near My Fingers - a"
    o1.album = "Elephant - a"
    o2.artist = "The White Stripes - b"
    o2.title = "The Air Near My Fingers - b"
    o2.album = "Elephant - b"
    r = s.get_dupe_groups([o1, o2])
    eq_(len(r), 1)


def test_tag_scan_with_different_scanned(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"track", "year"}
    o1 = no("foo")
    o2 = no("bar")
    o1.artist = "The White Stripes"
    o1.title = "some title"
    o1.track = "foo"
    o1.year = "bar"
    o2.artist = "The White Stripes"
    o2.title = "another title"
    o2.track = "foo"
    o2.year = "bar"
    r = s.get_dupe_groups([o1, o2])
    eq_(len(r), 1)


def test_tag_scan_only_scans_existing_tags(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"artist", "foo"}
    o1 = no("foo")
    o2 = no("bar")
    o1.artist = "The White Stripes"
    o1.foo = "foo"
    o2.artist = "The White Stripes"
    o2.foo = "bar"
    r = s.get_dupe_groups([o1, o2])
    eq_(len(r), 1)  # Because 'foo' is not scanned, they match


def test_tag_scan_converts_to_str(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"track"}
    o1 = no("foo")
    o2 = no("bar")
    o1.track = 42
    o2.track = 42
    try:
        r = s.get_dupe_groups([o1, o2])
    except TypeError:
        raise AssertionError()
    eq_(len(r), 1)


def test_tag_scan_non_ascii(fake_fileexists):
    s = Scanner()
    s.scan_type = ScanType.TAG
    s.scanned_tags = {"title"}
    o1 = no("foo")
    o2 = no("bar")
    o1.title = "foobar\u00e9"
    o2.title = "foobar\u00e9"
    try:
        r = s.get_dupe_groups([o1, o2])
    except UnicodeEncodeError:
        raise AssertionError()
    eq_(len(r), 1)


def test_ignore_list(fake_fileexists):
    s = Scanner()
    f1 = no("foobar")
    f2 = no("foobar")
    f3 = no("foobar")
    f1.path = Path("dir1/foobar")
    f2.path = Path("dir2/foobar")
    f3.path = Path("dir3/foobar")
    ignore_list = IgnoreList()
    ignore_list.ignore(str(f1.path), str(f2.path))
    ignore_list.ignore(str(f1.path), str(f3.path))
    r = s.get_dupe_groups([f1, f2, f3], ignore_list=ignore_list)
    eq_(len(r), 1)
    g = r[0]
    eq_(len(g.dupes), 1)
    assert f1 not in g
    assert f2 in g
    assert f3 in g
    # Ignored matches are not counted as discarded
    eq_(s.discarded_file_count, 0)


def test_ignore_list_checks_for_unicode(fake_fileexists):
    # scanner was calling path_str for ignore list checks. Since the Path changes, it must
    # be unicode(path)
    s = Scanner()
    f1 = no("foobar")
    f2 = no("foobar")
    f3 = no("foobar")
    f1.path = Path("foo1\u00e9")
    f2.path = Path("foo2\u00e9")
    f3.path = Path("foo3\u00e9")
    ignore_list = IgnoreList()
    ignore_list.ignore(str(f1.path), str(f2.path))
    ignore_list.ignore(str(f1.path), str(f3.path))
    r = s.get_dupe_groups([f1, f2, f3], ignore_list=ignore_list)
    eq_(len(r), 1)
    g = r[0]
    eq_(len(g.dupes), 1)
    assert f1 not in g
    assert f2 in g
    assert f3 in g


def test_file_evaluates_to_false(fake_fileexists):
    # A very wrong way to use any() was added at some point, causing resulting group list
    # to be empty.
    class FalseNamedObject(NamedObject):
        def __bool__(self):
            return False

    s = Scanner()
    f1 = FalseNamedObject("foobar", path="p1")
    f2 = FalseNamedObject("foobar", path="p2")
    r = s.get_dupe_groups([f1, f2])
    eq_(len(r), 1)


def test_size_threshold(fake_fileexists):
    # Only file equal or higher than the size_threshold in size are scanned
    s = Scanner()
    f1 = no("foo", 1, path="p1")
    f2 = no("foo", 2, path="p2")
    f3 = no("foo", 3, path="p3")
    s.size_threshold = 2
    groups = s.get_dupe_groups([f1, f2, f3])
    eq_(len(groups), 1)
    [group] = groups
    eq_(len(group), 2)
    assert f1 not in group
    assert f2 in group
    assert f3 in group


def test_tie_breaker_path_deepness(fake_fileexists):
    # If there is a tie in prioritization, path deepness is used as a tie breaker
    s = Scanner()
    o1, o2 = no("foo"), no("foo")
    o1.path = Path("foo")
    o2.path = Path("foo/bar")
    [group] = s.get_dupe_groups([o1, o2])
    assert group.ref is o2


def test_tie_breaker_copy(fake_fileexists):
    # if copy is in the words used (even if it has a deeper path), it becomes a dupe
    s = Scanner()
    o1, o2 = no("foo bar Copy"), no("foo bar")
    o1.path = Path("deeper/path")
    o2.path = Path("foo")
    [group] = s.get_dupe_groups([o1, o2])
    assert group.ref is o2


def test_tie_breaker_same_name_plus_digit(fake_fileexists):
    # if ref has the same words as dupe, but has some just one extra word which is a digit, it
    # becomes a dupe
    s = Scanner()
    o1 = no("foo bar 42")
    o2 = no("foo bar [42]")
    o3 = no("foo bar (42)")
    o4 = no("foo bar {42}")
    o5 = no("foo bar")
    # all numbered names have deeper paths, so they'll end up ref if the digits aren't correctly
    # used as tie breakers
    o1.path = Path("deeper/path")
    o2.path = Path("deeper/path")
    o3.path = Path("deeper/path")
    o4.path = Path("deeper/path")
    o5.path = Path("foo")
    [group] = s.get_dupe_groups([o1, o2, o3, o4, o5])
    assert group.ref is o5


def test_partial_group_match(fake_fileexists):
    # Count the number of discarded matches (when a file doesn't match all other dupes of the
    # group) in Scanner.discarded_file_count
    s = Scanner()
    o1, o2, o3 = no("a b"), no("a"), no("b")
    s.min_match_percentage = 50
    [group] = s.get_dupe_groups([o1, o2, o3])
    eq_(len(group), 2)
    assert o1 in group
    # The file that will actually be counted as a dupe is undefined. The only thing we want to test
    # is that we don't have both
    if o2 in group:
        assert o3 not in group
    else:
        assert o3 in group
    eq_(s.discarded_file_count, 1)


def test_dont_group_files_that_dont_exist(tmpdir):
    # when creating groups, check that files exist first. It's possible that these files have
    # been moved during the scan by the user.
    # In this test, we have to delete one of the files between the get_matches() part and the
    # get_groups() part.
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    p = Path(str(tmpdir))
    with p.joinpath("file1").open("w") as fp:
        fp.write("foo")
    with p.joinpath("file2").open("w") as fp:
        fp.write("foo")
    file1, file2 = fs.get_files(p)

    def getmatches(*args, **kw):
        file2.path.unlink()
        return [Match(file1, file2, 100)]

    s._getmatches = getmatches

    assert not s.get_dupe_groups([file1, file2])


def test_folder_scan_exclude_subfolder_matches(fake_fileexists):
    # when doing a Folders scan type, don't include matches for folders whose parent folder already
    # match.
    s = Scanner()
    s.scan_type = ScanType.FOLDERS
    topf1 = no("top folder 1", size=42)
    topf1.digest = topf1.digest_partial = topf1.digest_samples = b"some_digest__1"
    topf1.path = Path("/topf1")
    topf2 = no("top folder 2", size=42)
    topf2.digest = topf2.digest_partial = topf2.digest_samples = b"some_digest__1"
    topf2.path = Path("/topf2")
    subf1 = no("sub folder 1", size=41)
    subf1.digest = subf1.digest_partial = subf1.digest_samples = b"some_digest__2"
    subf1.path = Path("/topf1/sub")
    subf2 = no("sub folder 2", size=41)
    subf2.digest = subf2.digest_partial = subf2.digest_samples = b"some_digest__2"
    subf2.path = Path("/topf2/sub")
    eq_(len(s.get_dupe_groups([topf1, topf2, subf1, subf2])), 1)  # only top folders
    # however, if another folder matches a subfolder, keep in in the matches
    otherf = no("other folder", size=41)
    otherf.digest = otherf.digest_partial = otherf.digest_samples = b"some_digest__2"
    otherf.path = Path("/otherfolder")
    eq_(len(s.get_dupe_groups([topf1, topf2, subf1, subf2, otherf])), 2)


def test_ignore_files_with_same_path(fake_fileexists):
    # It's possible that the scanner is fed with two file instances pointing to the same path. One
    # of these files has to be ignored
    s = Scanner()
    f1 = no("foobar", path="path1/foobar")
    f2 = no("foobar", path="path1/foobar")
    eq_(s.get_dupe_groups([f1, f2]), [])


def test_dont_count_ref_files_as_discarded(fake_fileexists):
    # To speed up the scan, we don't bother comparing contents of files that are both ref files.
    # However, this causes problems in "discarded" counting and we make sure here that we don't
    # report discarded matches in exact duplicate scans.
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    o1 = no("foo", path="p1")
    o2 = no("foo", path="p2")
    o3 = no("foo", path="p3")
    o1.digest = o1.digest_partial = o1.digest_samples = "foobar"
    o2.digest = o2.digest_partial = o2.digest_samples = "foobar"
    o3.digest = o3.digest_partial = o3.digest_samples = "foobar"
    o1.is_ref = True
    o2.is_ref = True
    eq_(len(s.get_dupe_groups([o1, o2, o3])), 1)
    eq_(s.discarded_file_count, 0)


def test_prioritize_me(fake_fileexists):
    # in ScannerME, bitrate goes first (right after is_ref) in prioritization
    s = ScannerME()
    o1, o2 = no("foo", path="p1"), no("foo", path="p2")
    o1.bitrate = 1
    o2.bitrate = 2
    [group] = s.get_dupe_groups([o1, o2])
    assert group.ref is o2


# --- remove_dupe_paths tests


def test_remove_dupe_paths_samefile_receives_original_path(monkeypatch):
    # samefile must be called with the original (non-lowercased) path so that
    # case-sensitive filesystems can correctly distinguish Foo.txt from FOO.TXT.
    # Bug: the old code passed `normalized` (lowercased) as the first arg; on a
    # case-sensitive FS that path does not exist and samefile raises OSError,
    # causing the file to be silently dropped.
    samefile_calls = []

    def fake_samefile(a, b):
        samefile_calls.append((a, b))
        return True

    import os.path as _op
    monkeypatch.setattr(_op, "samefile", fake_samefile)

    f1 = no("Foo.txt", path="dir")
    f2 = no("FOO.TXT", path="dir")
    # Both normalise to the same lowercase key but have different original-case paths.
    f1.path = Path("dir/Foo.txt")
    f2.path = Path("dir/FOO.TXT")

    remove_dupe_paths([f1, f2])

    assert len(samefile_calls) == 1
    a, b = samefile_calls[0]
    # The first argument must be str(f2.path) — the original mixed-case path —
    # not the lowercased normalised form.
    expected_a = str(f2.path)
    lowercased_a = str(f2.path).lower()
    assert a == expected_a, (
        f"samefile first arg is {a!r}; expected original path {expected_a!r}, not lowercased {lowercased_a!r}"
    )


def _fs_is_case_sensitive(tmpdir):
    """Return True if the filesystem under tmpdir distinguishes Foo from foo."""
    p = Path(str(tmpdir))
    p.joinpath("CaseSentinelFile").touch()
    return not p.joinpath("casesentinelfile").exists()


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="case-insensitive filesystem")
def test_remove_dupe_paths_case_sensitive_fs_keeps_both_files(tmpdir):
    # On a case-sensitive filesystem, Foo.txt and foo.txt are different files and
    # must both survive remove_dupe_paths (regression for H4: samefile was called
    # with the lowercased path, causing FileNotFoundError -> silent drop).
    if not _fs_is_case_sensitive(tmpdir):
        pytest.skip("filesystem is not case-sensitive")

    p = Path(str(tmpdir))
    upper = p / "Foo.txt"
    lower = p / "foo.txt"
    upper.touch()
    lower.touch()

    f1 = NamedObject("Foo.txt")
    f1.path = upper
    f2 = NamedObject("foo.txt")
    f2.path = lower

    result = remove_dupe_paths([f1, f2])
    assert len(result) == 2, (
        "Both files should survive on a case-sensitive FS, but got: " + repr(result)
    )


# --- _apply_digest / big-file sampling tests


class _FakeFile:
    """Minimal stand-in for fs.File with the three digest attributes unset."""
    digest = None
    digest_partial = None
    digest_samples = None


FULL_HASH = b"fullhash"


def test_apply_digest_small_file_sets_all_three():
    # When bigsize is 0 (sampling disabled) all three fields should be set to
    # the full hash — the existing behaviour for non-threshold scans.
    f = _FakeFile()
    _apply_digest(f, FULL_HASH, size=1024, bigsize=0)
    assert f.digest == FULL_HASH
    assert f.digest_partial == FULL_HASH
    assert f.digest_samples == FULL_HASH


def test_apply_digest_file_at_threshold_sets_all_three():
    # A file exactly at the bigsize boundary is not "big", so all three are set.
    f = _FakeFile()
    bigsize = 100 * 1024 * 1024
    _apply_digest(f, FULL_HASH, size=bigsize, bigsize=bigsize)
    assert f.digest == FULL_HASH
    assert f.digest_partial == FULL_HASH
    assert f.digest_samples == FULL_HASH


def test_apply_digest_big_file_only_sets_digest():
    # Regression for M3: a file larger than bigsize must NOT have digest_partial or
    # digest_samples set to the full hash — those must remain None so that
    # fs.File._read_info computes them with the proper partial/sampling algorithm.
    f = _FakeFile()
    bigsize = 100 * 1024 * 1024
    _apply_digest(f, FULL_HASH, size=bigsize + 1, bigsize=bigsize)
    assert f.digest == FULL_HASH
    assert f.digest_partial is None, (
        "digest_partial must not be set from full hash for big files — "
        "getmatches_by_contents uses it as a cheap pre-filter"
    )
    assert f.digest_samples is None, (
        "digest_samples must not be set from full hash for big files — "
        "it defeats the big_file_size_threshold sampling optimisation"
    )


def test_parallel_hasher_preserves_big_file_sampling(fake_fileexists):
    # End-to-end: when big_file_size_threshold is set, two big files with the same
    # full hash but different samples must NOT match (digest_samples differs).
    # With the old code (all three set to full hash), they would incorrectly match.
    s = Scanner()
    s.scan_type = ScanType.CONTENTS
    bigsize = 100 * 1024 * 1024

    s.big_file_size_threshold = bigsize

    f = [no("bigfoo", bigsize + 1), no("bigbar", bigsize + 1)]
    # Same full digest and partial (would match under old broken code),
    # but different samples — so they must NOT form a group.
    f[0].digest = f[0].digest_partial = b"samehash"
    f[0].digest_samples = b"samples_a"
    f[1].digest = f[1].digest_partial = b"samehash"
    f[1].digest_samples = b"samples_b"

    groups = s.get_dupe_groups(f)
    eq_(len(groups), 0, "Files with different digest_samples must not match")


# --- M2: parallel hasher per-worker failure / fallback tests


class _StatResult:
    """Minimal os.stat_result stand-in."""
    def __init__(self, size, mtime_ns):
        self.st_size = size
        self.st_mtime_ns = mtime_ns


def _make_path(name):
    return Path("basepath") / name


def test_parallel_hasher_failed_worker_retried_sequentially(tmp_path):
    """A single worker failure must cause only that file to be retried, not files
    that already succeeded in the parallel phase."""
    GOOD_HASH = b"goodhash"
    RETRY_HASH = b"retryhash"

    good_path = tmp_path / "good.bin"
    bad_path = tmp_path / "bad.bin"
    good_path.write_bytes(b"x" * 100)
    bad_path.write_bytes(b"y" * 100)

    class _FakeFile2:
        digest = None
        digest_partial = None
        digest_samples = None
        def __init__(self, path, size=100):
            self.path = path
            self.size = size
            self.name = path.name

    f_good = _FakeFile2(good_path)
    f_bad = _FakeFile2(bad_path)

    scanner = Scanner()
    scanner.parallel_scan = True

    hashed_sequentially = []

    def fake_hash_file_worker(path_str):
        if path_str == str(bad_path):
            hashed_sequentially.append(path_str)
            return (path_str, RETRY_HASH)
        return (path_str, GOOD_HASH)

    import concurrent.futures

    class _FakePoolFuture:
        def __init__(self, path_str, exc=None):
            self._path = path_str
            self._exc = exc
        def result(self):
            if self._exc:
                raise self._exc
            return (self._path, GOOD_HASH)

    # Build fake futures: good succeeds, bad raises
    fake_futures = {
        _FakePoolFuture(str(good_path)): (f_good, 100, 1000),
        _FakePoolFuture(str(bad_path), exc=OSError("disk error")): (f_bad, 100, 1000),
    }

    def fake_as_completed(fmap):
        return list(fmap.keys())

    from core import hash_cache as hc_module
    import core.scanner as scanner_module

    class _FakePool:
        def __init__(self, max_workers):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def submit(self, fn, path_str):
            for fut, meta in fake_futures.items():
                f_obj, _, _ = meta
                if str(f_obj.path) == path_str:
                    return fut
            raise KeyError(path_str)

    with patch.object(hc_module.hashcachedb, "get", return_value=None), \
         patch.object(hc_module.hashcachedb, "set_batch"), \
         patch.object(hc_module.hashcachedb, "conn", new=object()), \
         patch("core.scanner.ProcessPoolExecutor", _FakePool), \
         patch("core.scanner.as_completed", fake_as_completed), \
         patch("core.scanner.hash_file_worker", fake_hash_file_worker):

        scanner._hash_files_parallel([f_good, f_bad], job.nulljob)

    assert f_good.digest == GOOD_HASH, "Successful parallel result must be applied"
    assert f_bad.digest == RETRY_HASH, "Failed worker must be retried sequentially"
    assert hashed_sequentially == [str(bad_path)], \
        "Only the failed file should be passed to sequential fallback"


def test_parallel_pool_failure_falls_back_to_sequential(tmp_path):
    """If the whole pool raises before any futures complete, all cache-miss files
    must be hashed sequentially and the output must be correct."""
    FALLBACK_HASH = b"fallback"

    path_a = tmp_path / "a.bin"
    path_b = tmp_path / "b.bin"
    path_a.write_bytes(b"a" * 50)
    path_b.write_bytes(b"b" * 50)

    class _FakeFile3:
        digest = None
        digest_partial = None
        digest_samples = None
        def __init__(self, path, size=50):
            self.path = path
            self.size = size
            self.name = path.name

    f_a = _FakeFile3(path_a)
    f_b = _FakeFile3(path_b)

    scanner = Scanner()
    scanner.parallel_scan = True

    sequentially_hashed = []

    def fake_hash_file_worker(path_str):
        sequentially_hashed.append(path_str)
        return (path_str, FALLBACK_HASH)

    class _CrashingPool:
        def __init__(self, max_workers):
            pass
        def __enter__(self):
            raise RuntimeError("pool spawn failed")
        def __exit__(self, *a):
            return False

    from core import hash_cache as hc_module

    with patch.object(hc_module.hashcachedb, "get", return_value=None), \
         patch.object(hc_module.hashcachedb, "set_batch"), \
         patch.object(hc_module.hashcachedb, "conn", new=object()), \
         patch("core.scanner.ProcessPoolExecutor", _CrashingPool), \
         patch("core.scanner.hash_file_worker", fake_hash_file_worker):

        scanner._hash_files_parallel([f_a, f_b], job.nulljob)

    assert f_a.digest == FALLBACK_HASH
    assert f_b.digest == FALLBACK_HASH
    assert set(sequentially_hashed) == {str(path_a), str(path_b)}, \
        "All cache-miss files must be retried after pool-level failure"


def test_parallel_pool_mid_crash_skips_already_completed_files(tmp_path):
    """When the pool crashes mid-run (after some futures complete), only files
    NOT in completed_paths should be retried sequentially."""
    DONE_HASH = b"donehash"
    FALLBACK_HASH = b"fallback"

    done_path = tmp_path / "done.bin"
    pending_path = tmp_path / "pending.bin"
    done_path.write_bytes(b"a" * 50)
    pending_path.write_bytes(b"b" * 50)

    class _FakeFile4:
        digest = None
        digest_partial = None
        digest_samples = None
        def __init__(self, path, size=50):
            self.path = path
            self.size = size
            self.name = path.name

    f_done = _FakeFile4(done_path)
    f_pending = _FakeFile4(pending_path)

    scanner = Scanner()
    scanner.parallel_scan = True

    sequentially_hashed = []

    def fake_hash_file_worker(path_str):
        sequentially_hashed.append(path_str)
        return (path_str, FALLBACK_HASH)

    # Fake future: done_path succeeds; pending_path is never yielded (crash mid-loop).
    class _GoodFuture:
        def result(self):
            return (str(done_path), DONE_HASH)

    class _MidCrashPool:
        def __init__(self, max_workers):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def submit(self, fn, path_str):
            return _GoodFuture()

    fake_futures_map = {_GoodFuture(): (f_done, 50, 1000)}

    def fake_as_completed_mid_crash(fmap):
        # Yield the one good future, then blow up (simulating pool crash mid-loop).
        yield _GoodFuture()
        raise RuntimeError("pool crashed after first result")

    from core import hash_cache as hc_module

    with patch.object(hc_module.hashcachedb, "get", return_value=None), \
         patch.object(hc_module.hashcachedb, "set_batch"), \
         patch.object(hc_module.hashcachedb, "conn", new=object()), \
         patch("core.scanner.ProcessPoolExecutor", _MidCrashPool), \
         patch("core.scanner.as_completed", fake_as_completed_mid_crash), \
         patch("core.scanner.hash_file_worker", fake_hash_file_worker):

        # We need future_to_meta to map the yielded future to f_done.
        # Patch submit so the yielded future matches what as_completed returns.
        orig_submit = _MidCrashPool.submit
        yielded = _GoodFuture()

        def patched_submit(self_pool, fn, path_str):
            # Return the same object that as_completed will yield for done_path.
            if path_str == str(done_path):
                return yielded
            return _GoodFuture()  # never yielded

        def patched_as_completed(fmap):
            # Yield done_path's future, then crash.
            if yielded in fmap:
                yield yielded
            raise RuntimeError("pool crashed after first result")

        with patch.object(_MidCrashPool, "submit", patched_submit), \
             patch("core.scanner.as_completed", patched_as_completed):

            scanner._hash_files_parallel([f_done, f_pending], job.nulljob)

    assert f_done.digest == DONE_HASH, "Completed parallel result must be preserved"
    assert f_pending.digest == FALLBACK_HASH, "Unfinished file must be retried sequentially"
    assert str(done_path) not in sequentially_hashed, \
        "File that completed in parallel must not be re-hashed sequentially"
    assert str(pending_path) in sequentially_hashed
