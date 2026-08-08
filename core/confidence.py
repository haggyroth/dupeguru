# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""How much is known about a group, so bulk action can go where it is defensible (issue #124).

Every group is presented the same way, but they are not equally understood. Two byte-identical
files with the same name, one of them inside a folder the user marked as Reference, is not the
same decision as two files that agreed on a sampled hash. The table shows both as a group with
a percentage, so the user either reviews everything at one care level or marks in bulk and
accepts a risk that was never shown to them.

Nothing here is newly computed. Match kind, the partial-hash flag and reference-folder state
are all already known; what was missing was combining them onto one axis and saying it out loud.

**The tiers are named for the evidence, not for a level of safety.** "Safe" is a promise, and a
group wrongly called safe is exactly the failure this is meant to prevent -- bulk-marking would
become a trap rather than a shortcut. So a tier says what was established and leaves the user to
decide what that is worth:

- CORROBORATED -- the content was compared in full, and something independent agrees
- CONTENT      -- the content was compared in full, and that is all that is known
- UNCONFIRMED  -- the content was never compared in full

Same principle as :class:`~core.engine.MatchKind`: describe the evidence, understate rather than
overstate, and let the reader judge.
"""

from typing import NamedTuple

from core.engine import MatchKind


class Confidence:
    """What is known about a group, weakest first."""

    UNCONFIRMED = "unconfirmed"
    CONTENT = "content"
    CORROBORATED = "corroborated"

    #: Weakest first. Comparisons and "which tier wins" both read off this.
    ORDER = (UNCONFIRMED, CONTENT, CORROBORATED)

    LABELS = {
        UNCONFIRMED: "Unconfirmed",
        CONTENT: "Content only",
        CORROBORATED: "Corroborated",
    }

    #: What each tier actually claims, for tooltips and `--plan`. Deliberately spelled out:
    #: the shorthand labels are the part a user could read a promise into.
    EXPLANATIONS = {
        UNCONFIRMED: "the contents were not compared in full",
        CONTENT: "identical contents, and nothing else corroborates",
        CORROBORATED: "identical contents, and something independent agrees",
    }

    @classmethod
    def weakest(cls, tiers) -> str:
        """The least-known tier among *tiers*, or UNCONFIRMED if there are none.

        A group is only as understood as its weakest pair. Taking the strongest instead would
        let one confirmed pair vouch for a resemblance sitting in the same group.
        """
        tiers = list(tiers)
        if not tiers:
            return cls.UNCONFIRMED
        return min(tiers, key=cls.ORDER.index)


class GroupConfidence(NamedTuple):
    """A group's tier, and why it landed there."""

    tier: str
    reason: str

    @property
    def label(self) -> str:
        return Confidence.LABELS[self.tier]


def classify_match(match) -> str:
    """What one pair establishes: CONTENT if its bytes were compared in full, else UNCONFIRMED.

    A pair can be EXACT and still not have been compared in full -- a big file matched on a
    sampled hash is recorded as ``kind=EXACT, partial=True``, meaning "believed identical, only
    sampled". Kind alone is therefore not enough, and reading it alone would promote every
    sampled match to the tier that invites bulk deletion.
    """
    kind = getattr(match, "kind", MatchKind.METADATA)
    if kind != MatchKind.EXACT:
        return Confidence.UNCONFIRMED
    if getattr(match, "partial", False):
        return Confidence.UNCONFIRMED
    return Confidence.CONTENT


def corroboration_of(group) -> str:
    """An independent reason to believe a copy in *group* is redundant, or "" if there is none.

    Independent means "not another look at the same bytes". Identical contents say the files are
    interchangeable; they say nothing about whether both were meant to be there. Two copies of a
    document deliberately placed in two projects are byte-identical and both wanted.

    Two signals qualify, and both are conservative:

    - **The user said so.** A member inside a folder marked Reference is the user's own statement
      that this is where the originals live. dupeGuru already refuses to delete those, so the
      remaining members are exactly what the arrangement was set up to remove.
    - **Every member shares one filename.** Same name in different folders is the shape a copy
      operation leaves. Required of *every* member rather than just one pair -- a member with a
      different name is one whose redundancy nothing corroborated, and it would be marked too.

    Near-names (``report (1).pdf`` beside ``report.pdf``) deliberately do not count. That is a
    guess about intent dressed up as evidence.
    """
    members = list(group)
    if len(members) < 2:
        return ""
    if any(getattr(member, "is_ref", False) for member in members):
        return "a copy is in a folder marked Reference"
    names = {member.name for member in members}
    if len(names) == 1:
        return "every copy has the same filename"
    return ""


def classify_group(group) -> GroupConfidence:
    """What is known about *group*, and why.

    Corroboration can only lift a group that already compared its contents in full. It never
    promotes an UNCONFIRMED group: two images that merely resemble each other are still only a
    resemblance when they happen to share a filename, and a matching name is precisely what a
    re-encode or a copy-and-edit leaves behind. Letting a name vouch for unread bytes would put
    files nobody compared into the pile meant for bulk action.
    """
    matches = getattr(group, "matches", None)
    if not matches:
        return GroupConfidence(Confidence.UNCONFIRMED, "nothing links this group")

    tier = Confidence.weakest(classify_match(match) for match in matches)
    if tier == Confidence.UNCONFIRMED:
        return GroupConfidence(tier, Confidence.EXPLANATIONS[tier])

    reason = corroboration_of(group)
    if reason:
        return GroupConfidence(Confidence.CORROBORATED, reason)
    return GroupConfidence(Confidence.CONTENT, Confidence.EXPLANATIONS[Confidence.CONTENT])


def tally(groups) -> dict:
    """How many groups sit in each tier, keyed by tier. Every tier is present, even at zero.

    A tier missing from the count reads as "none of those" only if you already know the tier
    exists, so the zeroes are kept.
    """
    counts = {tier: 0 for tier in Confidence.ORDER}
    for group in groups:
        counts[classify_group(group).tier] += 1
    return counts
