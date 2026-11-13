# Created By: Virgil Dupras
# Created On: 2006/02/23
# Copyright 2015 Hardcoded Software (http://www.hardcoded.net)
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

import io
import logging
import re
import os
import os.path as op
from errno import EISDIR, EACCES
from xml.etree import ElementTree as ET
import xml.sax.saxutils as _saxutils

from hscommon.jobprogress.job import nulljob
from hscommon.conflict import get_conflicted_name
from hscommon.util import flatten, nonone, FileOrPath, format_size
from hscommon.trans import tr

from core import engine
from core.markable import Markable

# Characters forbidden in XML 1.0 attribute values (control chars, surrogates, non-chars).
_XML_INVALID_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _rebuild_classed_group(group_elem, saved_files):
    """A group of identical-file classes from its saved form, or None when it cannot be rebuilt.

    ``<class members="0,1,2">`` lists the saved positions of one class; every file in no such
    element is a class of its own. ``<match>`` elements give the percentage between two classes,
    named by one member of each. Anything inconsistent -- a missing file that leaves two classes
    with no recorded match, an index out of range -- returns None, and the caller falls back to
    treating it as an ordinary group rather than showing invented percentages.
    """
    try:
        class_of = {}
        classes = []
        for class_elem in group_elem.iter("class"):
            members = [saved_files[int(i)] for i in class_elem.get("members", "").split(",") if i != ""]
            members = [m for m in members if m is not None]
            if len(members) < 2:
                # Too few left to be a class; its survivors fall through as singletons below.
                continue
            for member in members:
                class_of[id(member)] = len(classes)
            classes.append(members)
        # Singletons, in saved order alongside the classes, so class positions are stable.
        ordered_classes = []
        emitted = set()
        for file in saved_files:
            if file is None:
                continue
            index = class_of.get(id(file))
            if index is None:
                ordered_classes.append([file])
            elif index not in emitted:
                emitted.add(index)
                ordered_classes.append(classes[index])
        position = {id(m): i for i, members in enumerate(ordered_classes) for m in members}
        between = {}
        for match_elem in group_elem.iter("match"):
            attrs = match_elem.attrib
            first, second = saved_files[int(attrs["first"])], saved_files[int(attrs["second"])]
            if first is None or second is None:
                continue
            low, high = sorted((position[id(first)], position[id(second)]))
            between[(low, high)] = int(attrs["percentage"])
        return engine.Group.from_classes(ordered_classes, between)
    except (IndexError, KeyError, ValueError):
        return None


class Results(Markable):
    """Manages a collection of duplicate :class:`~core.engine.Group`.

    This class takes care or marking, sorting and filtering duplicate groups.

    .. attribute:: groups

        The list of :class:`~core.engine.Group` contained managed by this instance.

    .. attribute:: dupes

        A list of all duplicates (:class:`~core.fs.File` instances), without ref, contained in the
        currently managed :attr:`groups`.
    """

    # ---Override
    def __init__(self, app):
        Markable.__init__(self)
        self.__groups = []
        self.__group_of_duplicate = {}
        self.__groups_sort_descriptor = None  # This is a tuple (key, asc)
        self.__dupes = None
        self.__dupes_sort_descriptor = None  # This is a tuple (key, asc, delta)
        self.__filters = None
        self.__filtered_dupes = None
        self.__filtered_groups = None
        self.__recalculate_stats()
        self.__marked_size = 0
        self.app = app
        self.problems = []  # (dupe, error_msg)
        self.is_modified = False
        self.refresh_required = False

    def _did_mark(self, dupe):
        self.__marked_size += dupe.size

    def _did_unmark(self, dupe):
        self.__marked_size -= dupe.size

    def _get_markable_count(self):
        return self.__total_count

    def _is_markable(self, dupe):
        if dupe.is_ref:
            return False
        g = self.get_group_of_duplicate(dupe)
        if not g:
            return False
        if dupe is g.ref:
            return False
        if self.__filtered_dupes and dupe not in self.__filtered_dupes:
            return False
        return True

    def mark_all(self):
        if self.__filters:
            self.mark_multiple(self.__filtered_dupes)
        else:
            Markable.mark_all(self)

    def mark_invert(self):
        if self.__filters:
            self.mark_toggle_multiple(self.__filtered_dupes)
        else:
            Markable.mark_invert(self)

    def mark_none(self):
        if self.__filters:
            self.unmark_multiple(self.__filtered_dupes)
        else:
            Markable.mark_none(self)

    # ---Private
    def __get_dupe_list(self):
        if self.__dupes is None or self.refresh_required:
            self.__dupes = flatten(group.dupes for group in self.groups)
            self.refresh_required = False
            if None in self.__dupes:
                # This is debug logging to try to figure out #44
                logging.warning(
                    "There is a None value in the Results' dupe list. dupes: %r groups: %r",
                    self.__dupes,
                    self.groups,
                )
            if self.__filtered_dupes:
                self.__dupes = [dupe for dupe in self.__dupes if dupe in self.__filtered_dupes]
            sd = self.__dupes_sort_descriptor
            if sd:
                self.sort_dupes(sd[0], sd[1], sd[2])
        return self.__dupes

    def __get_groups(self):
        if self.__filtered_groups is None:
            return self.__groups
        else:
            return self.__filtered_groups

    def __get_stat_line(self):
        if self.__filtered_dupes is None:
            mark_count = self.mark_count
            marked_size = self.__marked_size
            total_count = self.__total_count
            total_size = self.__total_size
        else:
            mark_count = len([dupe for dupe in self.__filtered_dupes if self.is_marked(dupe)])
            marked_size = sum(dupe.size for dupe in self.__filtered_dupes if self.is_marked(dupe))
            total_count = len([dupe for dupe in self.__filtered_dupes if self.is_markable(dupe)])
            total_size = sum(dupe.size for dupe in self.__filtered_dupes if self.is_markable(dupe))
        if self.mark_inverted:
            marked_size = self.__total_size - marked_size
        result = tr("%d / %d (%s / %s) duplicates marked.") % (
            mark_count,
            total_count,
            format_size(marked_size, 2),
            format_size(total_size, 2),
        )
        if self.__filters:
            result += tr(" filter: %s") % " --> ".join(self.__filters)
        return result

    def __recalculate_stats(self):
        self.__total_size = 0
        self.__total_count = 0
        for group in self.groups:
            markable = [dupe for dupe in group.dupes if self._is_markable(dupe)]
            self.__total_count += len(markable)
            self.__total_size += sum(dupe.size for dupe in markable)

    def __set_groups(self, new_groups):
        self.mark_none()
        self.__groups = new_groups
        self.__group_of_duplicate = {}
        for g in self.__groups:
            for dupe in g:
                self.__group_of_duplicate[dupe] = g
                if not hasattr(dupe, "is_ref"):
                    dupe.is_ref = False
        self.is_modified = bool(self.__groups)
        old_filters = nonone(self.__filters, [])
        self.apply_filter(None)
        for filter_str in old_filters:
            self.apply_filter(filter_str)

    # ---Public
    def apply_filter(self, filter_str):
        """Applies a filter ``filter_str`` to :attr:`groups`

        When you apply the filter, only  dupes with the filename matching ``filter_str`` will be in
        in the results. To cancel the filter, just call apply_filter with ``filter_str`` to None,
        and the results will go back to normal.

        If call apply_filter on a filtered results, the filter will be applied
        *on the filtered results*.

        :param str filter_str: a string containing a regexp to filter dupes with.
        """
        if not filter_str:
            self.__filtered_dupes = None
            self.__filtered_groups = None
            self.__filters = None
        else:
            if not self.__filters:
                self.__filters = []
            try:
                filter_re = re.compile(filter_str, re.IGNORECASE)
            except re.error:
                return  # don't apply this filter.
            self.__filters.append(filter_str)
            if self.__filtered_dupes is None:
                self.__filtered_dupes = flatten(g[:] for g in self.groups)
            self.__filtered_dupes = {dupe for dupe in self.__filtered_dupes if filter_re.search(str(dupe.path))}
            filtered_groups = set()
            for dupe in self.__filtered_dupes:
                filtered_groups.add(self.get_group_of_duplicate(dupe))
            self.__filtered_groups = list(filtered_groups)
        self.__recalculate_stats()
        sd = self.__groups_sort_descriptor
        if sd:
            self.sort_groups(sd[0], sd[1])
        self.__dupes = None

    def get_group_of_duplicate(self, dupe):
        """Returns :class:`~core.engine.Group` in which ``dupe`` belongs."""
        try:
            return self.__group_of_duplicate[dupe]
        except (TypeError, KeyError):
            return None

    is_markable = _is_markable

    def load_from_xml(self, infile, get_file, j=nulljob):
        """Load results from ``infile``.

        :param infile: a file or path pointing to an XML file created with :meth:`save_to_xml`.
        :param get_file: a function f(path) returning a :class:`~core.fs.File` wrapping the path.
        :param j: A :ref:`job progress instance <jobs>`.
        """

        def do_match(dupes, group):
            for i, ref in enumerate(dupes[:-1]):
                for other in dupes[i + 1 :]:
                    group.add_match(engine.get_match(ref, other))

        self.apply_filter(None)
        root = ET.parse(infile).getroot()
        group_elems = list(root.iter("group"))
        groups = []
        marked = set()
        for group_elem in j.iter_with_progress(group_elems, every=100):
            group = engine.Group()
            dupes = []
            # Parallel to the saved file elements, None where a file is gone. A class refers to
            # its members by their position in the saved list, which `dupes` (only the files that
            # still exist) does not preserve.
            saved_files = []
            for file_elem in group_elem.iter("file"):
                path = file_elem.get("path")
                words = file_elem.get("words", "")
                if not path:
                    saved_files.append(None)
                    continue
                file = get_file(path)
                if file is None:
                    saved_files.append(None)
                    continue
                file.words = words.split(",")
                file.is_ref = file_elem.get("is_ref") == "y"
                dupes.append(file)
                saved_files.append(file)
                if file_elem.get("marked") == "y":
                    marked.add(file)
            if group_elem.get("uniform") == "y" and len(dupes) >= 2:
                # Written by a version that records an identical group's uniformity rather than
                # its pairs. Rebuilding it here is what keeps the content-match values -- the
                # do_match fallback below computes a *name* match, which would silently turn a
                # 100% content duplicate into whatever its filenames happen to score, and lose
                # the partial flag with it.
                try:
                    percentage = int(group_elem.get("percentage", 100))
                except ValueError:
                    percentage = 100
                kind = group_elem.get("kind", engine.MatchKind.METADATA)
                if kind not in (engine.MatchKind.EXACT, engine.MatchKind.RESEMBLANCE, engine.MatchKind.METADATA):
                    # Written by something newer, or edited. Understating is the safe direction:
                    # an unrecognised kind must not be read as a stronger claim than it is.
                    kind = engine.MatchKind.METADATA
                group = engine.Group.from_identical(dupes, percentage, group_elem.get("partial") == "y", kind)
            elif group_elem.get("classed") == "y" and len(dupes) >= 2:
                rebuilt = _rebuild_classed_group(group_elem, saved_files)
                if rebuilt is not None:
                    group = rebuilt
            for match_elem in () if group.is_derived else group_elem.iter("match"):
                try:
                    attrs = match_elem.attrib
                    first_file = dupes[int(attrs["first"])]
                    second_file = dupes[int(attrs["second"])]
                    percentage = int(attrs["percentage"])
                    # Absent in results saved before partial matches were recorded; those
                    # predate partial hashing being surfaced at all, so False is correct.
                    partial = attrs.get("partial") == "y"
                    group.add_match(engine.Match(first_file, second_file, percentage, partial))
                except (IndexError, KeyError, ValueError):
                    # Covers missing attr, non-int values and indexes out of bounds
                    pass
            if (not group.matches) and (len(dupes) >= 2):
                do_match(dupes, group)
            group.prioritize(lambda x: dupes.index(x))
            if len(group):
                groups.append(group)
            j.add_progress()
        self.groups = groups
        for dupe_file in marked:
            self.mark(dupe_file)
        self.is_modified = False

    def make_ref(self, dupe):
        """Make ``dupe`` take the :attr:`~core.engine.Group.ref` position of its group."""
        g = self.get_group_of_duplicate(dupe)
        r = g.ref
        if not g.switch_ref(dupe):
            return False
        self._remove_mark_flag(dupe)
        if not r.is_ref:
            self.__total_count += 1
            self.__total_size += r.size
        if not dupe.is_ref:
            self.__total_count -= 1
            self.__total_size -= dupe.size
        self.__dupes = None
        self.is_modified = True
        return True

    def perform_on_marked(self, func, remove_from_results):
        """Performs ``func`` on all marked dupes.

        If an ``EnvironmentError`` is raised during the call, the problematic dupe is added to
        self.problems.

        :param bool remove_from_results: If true, dupes which had ``func`` applied and didn't cause
                                         any problem.
        """
        self.problems = []
        to_remove = []
        marked = (dupe for dupe in self.dupes if self.is_marked(dupe))
        for dupe in marked:
            try:
                func(dupe)
                to_remove.append(dupe)
            except (OSError, UnicodeEncodeError) as e:
                self.problems.append((dupe, str(e)))
            except Exception as e:
                # Deliberately broad. An unexpected exception here used to unwind the whole
                # loop, so every dupe already processed was moved or deleted on disk but never
                # removed from the results below -- the table then disagreed with the
                # filesystem, and a second run operated on entries whose sources were gone.
                # Recording it keeps the batch consistent; the traceback still reaches the log.
                logging.exception("Unexpected error while processing %r", dupe)
                self.problems.append((dupe, str(e)))
        if remove_from_results:
            self.remove_duplicates(to_remove)
            self.mark_none()
            for dupe, _ in self.problems:
                self.mark(dupe)

    def perform_on_marked_batched(self, batch_func, remove_from_results, batch_size, on_chunk=None):
        """Like :meth:`perform_on_marked`, for work that is cheaper done on many dupes at once.

        ``batch_func(dupes)`` takes a chunk of marked dupes and returns one entry per dupe, in
        order: ``None`` for success or the exception that file hit. Failures are recorded and
        removed exactly as :meth:`perform_on_marked` does, so the two are interchangeable to the
        caller. ``on_chunk(dupes)`` is called before each chunk (for progress); if it raises
        ``JobCancelled`` the remaining chunks are not started and their dupes stay marked, which is
        what stopping should mean, rather than recording each of them as a problem.
        """
        from hscommon.jobprogress.job import JobCancelled

        self.problems = []
        to_remove = []
        unreached = []
        marked = [dupe for dupe in self.dupes if self.is_marked(dupe)]
        for start in range(0, len(marked), batch_size):
            chunk = marked[start : start + batch_size]
            if on_chunk is not None:
                try:
                    on_chunk(chunk)
                except JobCancelled:
                    unreached = marked[start:]
                    break
            try:
                errors = batch_func(chunk)
            except Exception as error:
                # A bug rather than one file's failure. Some of the chunk may already be gone from
                # disk, so reporting every one as a problem is the cautious reading: a second run
                # finds the removed ones already gone and treats them as done.
                logging.exception("Unexpected error while processing a batch of %d", len(chunk))
                errors = [error] * len(chunk)
            for dupe, error in zip(chunk, errors):
                if error is None:
                    to_remove.append(dupe)
                elif isinstance(error, (OSError, UnicodeEncodeError)):
                    self.problems.append((dupe, str(error)))
                else:
                    logging.error("Unexpected error while processing %r", dupe, exc_info=error)
                    self.problems.append((dupe, str(error)))
        if remove_from_results:
            self.remove_duplicates(to_remove)
            self.mark_none()
            for dupe, _ in self.problems:
                self.mark(dupe)
            # Marks are cleared after a run, so files a cancel never reached would otherwise lose
            # theirs and look as though they had been dealt with.
            for dupe in unreached:
                self.mark(dupe)

    def has_marked_partial_matches(self) -> bool:
        """Return True if any marked duplicate was matched only by a partial (sampled) hash."""
        for dupe in self.dupes:
            if self.is_marked(dupe):
                group = self.get_group_of_duplicate(dupe)
                if group:
                    match = group.get_match_of(dupe)
                    if match and getattr(match, "partial", False):
                        return True
        return False

    def remove_duplicates(self, dupes):
        """Remove ``dupes`` from their respective :class:`~core.engine.Group`.

        Also, remove the group from :attr:`groups` if it ends up empty.
        """
        affected_groups = set()
        for dupe in dupes:
            group = self.get_group_of_duplicate(dupe)
            if dupe not in group.dupes:
                return
            ref = group.ref
            group.remove_dupe(dupe, False)
            del self.__group_of_duplicate[dupe]
            self._remove_mark_flag(dupe)
            self.__total_count -= 1
            self.__total_size = max(0, self.__total_size - dupe.size)
            if not group:
                del self.__group_of_duplicate[ref]
                self.__groups.remove(group)
                if self.__filtered_groups:
                    self.__filtered_groups.remove(group)
            else:
                affected_groups.add(group)
        for group in affected_groups:
            group.discard_matches()
        self.__dupes = None
        self.is_modified = bool(self.__groups)

    def save_to_xml(self, outfile):
        """Save results to ``outfile`` in XML.

        :param outfile: file object or path.
        """
        self.apply_filter(None)

        def do_write(outfile):
            with FileOrPath(outfile, "wb") as fp:
                # Use a text wrapper with write_through so bytes flow to fp immediately.
                # detach() in the finally block prevents the wrapper from closing fp on GC.
                text_fp = io.TextIOWrapper(fp, encoding="utf-8", newline="", write_through=True)
                gen = _saxutils.XMLGenerator(text_fp, encoding="utf-8", short_empty_elements=True)
                try:
                    gen.startDocument()
                    gen.startElement("results", {})
                    for g in self.groups:
                        # A group of identical files carries the same match between every pair,
                        # so recording the pairs writes k(k-1)/2 elements to say what the
                        # members and three values already say. On a cluster of 23,857 files
                        # that is 284 million elements and roughly 17 GB (issue #193).
                        if g.is_uniform:
                            gen.startElement(
                                "group",
                                {
                                    "uniform": "y",
                                    "percentage": str(int(g.matches.percentage)),
                                    "partial": "y" if g.matches.partial else "n",
                                    "kind": str(g.matches.kind),
                                },
                            )
                        elif g.is_derived:
                            # Classes of identical files with a percentage between each pair of
                            # classes. Writing the pairs would be quadratic in the class sizes.
                            gen.startElement("group", {"classed": "y"})
                        else:
                            gen.startElement("group", {})
                        dupe2index = {}
                        for index, d in enumerate(g):
                            dupe2index[d] = index
                            try:
                                words = ",".join(engine.unpack_fields(d.words))
                            except AttributeError:
                                words = ""
                            path = str(d.path)
                            if _XML_INVALID_CHARS.search(path):
                                path = ""
                            gen.startElement(
                                "file",
                                {
                                    "path": path,
                                    "words": words,
                                    "is_ref": "y" if d.is_ref else "n",
                                    "marked": "y" if self.is_marked(d) else "n",
                                },
                            )
                            gen.endElement("file")
                        if g.is_derived and not g.is_uniform:
                            classes = g.matches.classes()
                            for members in classes:
                                if len(members) > 1:
                                    gen.startElement(
                                        "class", {"members": ",".join(str(dupe2index[m]) for m in members)}
                                    )
                                    gen.endElement("class")
                            for low, high, percentage in g.matches.class_percentages():
                                gen.startElement(
                                    "match",
                                    {
                                        "first": str(dupe2index[classes[low][0]]),
                                        "second": str(dupe2index[classes[high][0]]),
                                        "percentage": str(int(percentage)),
                                        "partial": "n",
                                    },
                                )
                                gen.endElement("match")
                        elif not g.is_uniform:
                            for match in g.matches:
                                gen.startElement(
                                    "match",
                                    {
                                        "first": str(dupe2index[match.first]),
                                        "second": str(dupe2index[match.second]),
                                        "percentage": str(int(match.percentage)),
                                        "partial": "y" if match.partial else "n",
                                    },
                                )
                                gen.endElement("match")
                        gen.endElement("group")
                    gen.endElement("results")
                    gen.endDocument()
                    text_fp.flush()
                finally:
                    text_fp.detach()

        try:
            do_write(outfile)
        except OSError as e:
            # If our OSError is because dest is already a directory, we want to handle that. 21 is
            # the code we get on OS X and Linux (EISDIR), 13 is what we get on Windows (EACCES).
            if e.errno in (EISDIR, EACCES):
                p = str(outfile)
                dirname, basename = op.split(p)
                otherfiles = os.listdir(dirname)
                newname = get_conflicted_name(otherfiles, basename)
                do_write(op.join(dirname, newname))
            else:
                raise
        self.is_modified = False

    def sort_dupes(self, key, asc=True, delta=False):
        """Sort :attr:`dupes` according to ``key``.

        :param str key: key attribute name to sort with.
        :param bool asc: If false, sorting is reversed.
        :param bool delta: If true, sorting occurs using :ref:`delta values <deltavalues>`.
        """
        if not self.__dupes:
            self.__get_dupe_list()
        self.__dupes.sort(
            key=lambda d: self.app._get_dupe_sort_key(d, lambda: self.get_group_of_duplicate(d), key, delta),
            reverse=not asc,
        )
        self.__dupes_sort_descriptor = (key, asc, delta)

    def sort_groups(self, key, asc=True):
        """Sort :attr:`groups` according to ``key``.

        The :attr:`~core.engine.Group.ref` of each group is used to extract values for sorting.

        :param str key: key attribute name to sort with.
        :param bool asc: If false, sorting is reversed.
        """
        self.groups.sort(key=lambda g: self.app._get_group_sort_key(g, key), reverse=not asc)
        self.__groups_sort_descriptor = (key, asc)

    # ---Properties
    dupes = property(__get_dupe_list)
    groups = property(__get_groups, __set_groups)
    stat_line = property(__get_stat_line)
