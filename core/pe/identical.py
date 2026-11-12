# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Byte-identical pictures, found so they can be matched once instead of pairwise.

Picture matching compared every picture with every other and returned a ``Match`` per similar
pair. That is fine for photographs and ruinous for a folder holding the same image thousands of
times: the measured sample had one folder with ~2% of all pictures being copies of one image, and
extrapolating its pair count to the whole corpus gave hundreds of millions of matches for that
cluster alone. Content mode already avoids this by building *equivalence classes* (issue #180);
this does the same for pictures.

**Why this is not a new kind of matching.** What the pairwise path did for identical files is
fully determined, so nothing is approximated here:

- A pair of byte-identical pictures has identical block signatures, so it is found at distance 0
  and reported at 100% -- the digest check that caps a same-signature, different-bytes pair at
  99% is what makes "100%" mean *the same bytes*.
- ``Scanner.get_dupe_groups`` forms groups from matches in descending percentage order, so the
  100% pairs run first and every identical set becomes a group of its own before any 95% match is
  looked at. A later match between two files already in different groups is skipped, which means
  two identical sets that merely resemble each other stay two groups, while a file in no group
  yet can still join one.
- Every member of a class matches a third picture identically, so one representative standing in
  for its class gives the same answers.

So matching runs over one representative per class plus every picture that is alone, and the
classes are expanded again once the groups exist (``Scanner._expand_picture_classes``).

**What is deliberately not collapsed**, because the pairwise route handles it and collapsing
would have to reproduce subtler rules:

- a class containing a reference file: two reference files are never matched to each other, so
  such a class is not a clique of identical matches;
- anything when an ignore list is in force: it removes individual pairs;
- pictures with no orientation-0 signature, which the matcher never compared at all.
"""

import logging
from collections import defaultdict, namedtuple

from hscommon.util import get_file_ext

#: Options of the surrounding scan that decide which identical pictures may share a class.
#:
#: ``mix_file_kind`` False means a match across extensions is dropped, so a ``.jpg`` and a ``.png``
#: with the same bytes must not share a class (the pairwise route would never have grouped them).
#: ``check_exists`` mirrors ``include_exists_check``: classes are trimmed to members that exist,
#: because the pairwise route dropped every match naming a missing file.
Collapse = namedtuple("Collapse", "mix_file_kind check_exists")


def identical_classes(pictures, signature_of, options):
    """Partition *pictures* into classes of byte-identical files.

    *signature_of* returns the picture's block signature (anything hashable and equal exactly when
    the signatures are) or a falsy value for one that takes no part in matching. Returns a list of
    classes, each a list of two or more pictures in their original order, so the first is the
    representative the matcher will see.

    Files are bucketed by ``(size, signature)`` first and compared by full digest only inside a
    bucket of two or more, so a corpus of mostly different pictures reads almost nothing here.
    """
    buckets = defaultdict(list)
    for picture in pictures:
        signature = signature_of(picture)
        if not signature:
            continue
        buckets[(picture.size, signature)].append(picture)

    classes = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        by_digest = defaultdict(list)
        for picture in bucket:
            digest = picture.digest
            # An unreadable file has no digest, and two of those are not known to be identical.
            if digest:
                by_digest[digest].append(picture)
        for members in by_digest.values():
            if len(members) < 2:
                continue
            if any(getattr(member, "is_ref", False) for member in members):
                continue
            parts = [members]
            if not options.mix_file_kind:
                by_ext = defaultdict(list)
                for member in members:
                    by_ext[get_file_ext(member.name)].append(member)
                parts = list(by_ext.values())
            for part in parts:
                if options.check_exists:
                    part = [member for member in part if member.exists()]
                if len(part) >= 2:
                    classes.append(part)
    if classes:
        logging.info(
            "Collapsed %d identical picture(s) into %d class(es) before matching",
            sum(len(c) for c in classes),
            len(classes),
        )
    return classes


def representatives(pictures, classes):
    """*pictures* with every class replaced by its first member, order otherwise unchanged."""
    dropped = {id(member) for members in classes for member in members[1:]}
    return [picture for picture in pictures if id(picture) not in dropped]
