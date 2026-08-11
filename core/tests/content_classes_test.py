# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Equivalence classes say the same thing as pairwise matching, in linear space (issue #180).

A contents scan establishes that k files are identical. Saying so as k(k-1)/2 pairs costs
quadratic memory to express one fact: a cluster of 23,857 identical files -- an ordinary shape
for a photo archive -- needs 284 million Match objects, about 23 GiB of namedtuples, for a
single equivalence class.

The risk in replacing a comparison is not that it is slower. It is that it silently finds a
different set of duplicates, which would be invisible until someone deleted the wrong thing.
So the central test here is differential: over randomised corpora, expanding the classes must
reproduce *exactly* the matches the pairwise matcher returns. The hand-written cases below it
exist to pin the specific behaviours that randomisation is unlikely to generate often --
unreadable digests, reference pairs, the sampled-digest path.
"""

import random

from core.engine import (
    ContentClass,
    content_classes,
    getmatches_by_contents,
    matches_from_classes,
)
from core.tests.base import NamedObject


#: Distinguishes "not specified, default to the full digest" from "explicitly unreadable".
#: Using None for both would make it impossible to write a test for a missing digest, which is
#: exactly the case that must not group files together.
_DEFAULT = object()


def make(name, size=100, digest="d", partial=_DEFAULT, samples=_DEFAULT, is_ref=False):
    """A file whose digests can be set independently, as the real ones are read separately."""
    f = NamedObject(name, size=size)
    f.digest = digest
    f.digest_partial = digest if partial is _DEFAULT else partial
    f.digest_samples = digest if samples is _DEFAULT else samples
    f.is_ref = is_ref
    return f


def as_pairs(matches):
    """Matches as a comparable set: pair identity, plus the partial flag."""
    return {(frozenset((m.first.name, m.second.name)), m.partial) for m in matches}


class TestItAgreesWithPairwiseMatching:
    """The property that matters. Everything else is detail."""

    def _random_corpus(self, rng):
        files = []
        for i in range(rng.randint(2, 30)):
            files.append(
                make(
                    f"f{i}",
                    # Few distinct sizes and digests, so clusters and collisions actually occur.
                    size=rng.choice([0, 100, 100, 100, 5000]),
                    digest=rng.choice(["a", "b", "c"]),
                    partial=rng.choice(["p", "p", "q"]),
                    samples=rng.choice(["s", "s", "t"]),
                    is_ref=rng.random() < 0.2,
                )
            )
        return files

    def test_expanded_classes_equal_pairwise_matches(self):
        rng = random.Random(20260811)
        for _ in range(400):
            files = self._random_corpus(rng)
            for bigsize in (0, 1000):
                expected = as_pairs(getmatches_by_contents(list(files), bigsize=bigsize))
                actual = as_pairs(matches_from_classes(content_classes(list(files), bigsize=bigsize)))
                assert actual == expected, f"diverged on {[f.name for f in files]} bigsize={bigsize}"

    def test_the_corpora_actually_produce_matches(self):
        # A differential test over corpora that never match anything would pass trivially.
        rng = random.Random(1)
        produced = 0
        for _ in range(50):
            produced += len(getmatches_by_contents(self._random_corpus(rng)))
        assert produced > 100, "the random corpora are not exercising the matcher"


class TestTheClassesThemselves:
    def test_identical_files_form_one_class(self):
        files = [make(f"f{i}") for i in range(5)]
        [cls] = content_classes(files)
        assert sorted(f.name for f in cls.files) == ["f0", "f1", "f2", "f3", "f4"]

    def test_a_class_of_k_files_costs_k_not_k_squared(self):
        # The whole point. 800 identical files is one class of 800, where the pairwise matcher
        # produces 319,600 Match objects to say the same thing.
        files = [make(f"f{i}") for i in range(800)]
        classes = content_classes(files)
        assert len(classes) == 1
        assert len(classes[0].files) == 800
        assert len(getmatches_by_contents(files)) == 800 * 799 // 2

    def test_different_sizes_never_share_a_class(self):
        assert content_classes([make("a", size=10), make("b", size=20)]) == []

    def test_different_digests_never_share_a_class(self):
        assert content_classes([make("a", digest="x"), make("b", digest="y")]) == []

    def test_a_lone_file_is_not_a_class(self):
        assert content_classes([make("a")]) == []

    def test_zero_length_files_are_identical_without_reading(self):
        # The pairwise path short-circuits these before touching a digest; so does this.
        files = [make("a", size=0, digest=None), make("b", size=0, digest=None)]
        [cls] = content_classes(files)
        assert len(cls.files) == 2
        assert cls.partial is False

    def test_an_unreadable_digest_joins_nothing(self):
        # The pairwise path required "is not None" before comparing. Grouping on None would make
        # every unreadable file in a bucket identical to every other, which is the worst possible
        # direction to be wrong in.
        files = [make("a", digest=None), make("b", digest=None)]
        assert content_classes(files) == []

    def test_an_unreadable_partial_digest_joins_nothing(self):
        files = [make("a", partial=None), make("b", partial=None)]
        assert content_classes(files) == []


class TestSampledDigests:
    def test_a_big_file_class_is_marked_partial(self):
        files = [make(f"f{i}", size=5000, samples="s") for i in range(3)]
        [cls] = content_classes(files, bigsize=1000)
        assert cls.partial is True

    def test_a_small_file_class_is_not_partial(self):
        files = [make(f"f{i}", size=100) for i in range(3)]
        [cls] = content_classes(files, bigsize=1000)
        assert cls.partial is False

    def test_big_files_are_separated_by_their_samples_not_their_full_digest(self):
        # Above bigsize the pairwise path compares digest_samples and never reads the full
        # digest, so two files agreeing on samples belong together even if their full digests
        # were set differently.
        files = [
            make("a", size=5000, digest="different", partial="p", samples="same"),
            make("b", size=5000, digest="values", partial="p", samples="same"),
        ]
        [cls] = content_classes(files, bigsize=1000)
        assert sorted(f.name for f in cls.files) == ["a", "b"]

    def test_a_missing_sample_digest_joins_nothing(self):
        files = [make(f"f{i}", size=5000, samples=None) for i in range(2)]
        assert content_classes(files, bigsize=1000) == []


class TestExpansion:
    def test_two_reference_files_are_never_paired(self):
        # Load-bearing rather than an optimisation: it is what stops a group forming around two
        # files the user has protected from deletion.
        files = [make("r1", is_ref=True), make("r2", is_ref=True), make("x")]
        pairs = as_pairs(matches_from_classes(content_classes(files)))
        assert (frozenset({"r1", "r2"}), False) not in pairs
        assert len(pairs) == 2

    def test_the_partial_flag_survives_expansion(self):
        files = [make(f"f{i}", size=5000, samples="s") for i in range(2)]
        [match] = matches_from_classes(content_classes(files, bigsize=1000))
        assert match.partial is True

    def test_expansion_of_nothing_is_nothing(self):
        assert matches_from_classes([]) == []

    def test_a_class_can_be_built_directly(self):
        # The type is a plain namedtuple so callers can construct one in a test without a scan.
        a, b = make("a"), make("b")
        assert len(matches_from_classes([ContentClass([a, b], False)])) == 1
