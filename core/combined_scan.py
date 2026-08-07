# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Find visually similar images during a standard scan (issue #128).

"Mode" decides three things at once and the user only gets one choice: which file class
collects the files, which matcher runs, and which columns appear. Point dupeGuru at a folder
holding documents, videos and photos and Standard mode finds the byte-identical copies while
silently ignoring that two of the photos are the same picture at different sizes; Picture mode
finds those and collects nothing else at all.

This runs picture matching over the images in a standard scan and merges the results, so one
scan answers "find everything worth cleaning up here".

**The collected files are not photos.** A standard scan collects ``se.fs.File``, which has no
block signature and cannot be compared perceptually. So the images are re-read as photo
objects purely to be matched, and the matches are then reported against the *original* files.
Reporting the photo objects instead would put two objects per path into the results, and
everything downstream -- grouping, marking, deletion -- would be working on the wrong ones.

**Cost is why this is opt-in.** Content matching is I/O-bound and cheap on a warm cache;
picture matching decodes every image and compares near-quadratically. Measured at 15.9s
against 0.1s for the same file count.
"""

import logging

from core.engine import Match, MatchKind


def photo_class():
    """The platform's photo class, or None if no image decoder is available.

    Picture matching needs a Qt binding to decode images; the command line has none unless one
    happens to be installed. Returning None lets the caller say so and carry on with the
    content matches rather than failing the whole scan.
    """
    from core.pe import photo

    return photo.PLAT_SPECIFIC_PHOTO_CLASS


def photos_in(files, cls):
    """Photo objects for the images among *files*, keyed by the file they stand in for.

    The mapping is the point. These exist only to be matched; every result is reported against
    the file the scan actually collected.
    """
    photos = {}
    for file in files:
        try:
            if not cls.can_handle(file.path):
                continue
        except Exception:
            continue
        try:
            photo = cls(file.path)
        except Exception:
            logging.debug("Could not read %s as an image", file.path, exc_info=True)
            continue
        # Reference status has to travel with the stand-in. The picture matcher refuses to pair
        # two reference files, and a stand-in that did not know it stood for one would hand back
        # a match between two files the user has protected from deletion.
        photo.is_ref = getattr(file, "is_ref", False)
        photos[photo] = file
    return photos


def picture_matches_over(files, j, threshold=75, match_scaled=False, match_rotated=False, cache_path=None):
    """Perceptual matches among the images in *files*, reported against those same files.

    Returns an empty list rather than raising when there is no image decoder, or no images:
    a standard scan that finds no pictures should still report its content matches.
    """
    cls = photo_class()
    if cls is None:
        logging.info("No image decoder available; skipping picture matching in this scan")
        return []

    photos = photos_in(files, cls)
    if len(photos) < 2:
        return []

    from core.pe import matchblock

    matches = matchblock.getmatches(
        list(photos),
        cache_path=cache_path,
        threshold=threshold,
        match_scaled=match_scaled,
        match_rotated=match_rotated,
        j=j,
    )

    # Back to the collected files. A match between two stand-ins is a match between the files
    # they stand for, and only those belong in the results.
    translated = []
    for match in matches:
        first, second = photos.get(match.first), photos.get(match.second)
        if first is None or second is None:
            continue
        translated.append(Match(first, second, match.percentage, kind=MatchKind.RESEMBLANCE))
    return translated


def merge_matches(content_matches, picture_matches):
    """Both matchers' results, with each pair kept once.

    Two images that are byte-identical are found twice -- the content scan compares their bytes,
    the picture scan compares their appearance. The content match is the one to keep: it says
    something stronger about the same pair, and a pair reported twice would be reported at two
    different confidences.
    """
    seen = {(id(m.first), id(m.second)) for m in content_matches}
    seen |= {(id(m.second), id(m.first)) for m in content_matches}
    merged = list(content_matches)
    for match in picture_matches:
        if (id(match.first), id(match.second)) in seen:
            continue
        merged.append(match)
    return merged
