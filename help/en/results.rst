Results
=======

.. contents::

When dupeGuru is finished scanning for duplicates, it will show its results in the form of duplicate group list.

About duplicate groups
----------------------

A duplicate group is a group of files that all match together. Every group has a **reference file** and one or more **duplicate files**. The reference file is the first file of the group. Its mark box is disabled. Below it, and indented, are the duplicate files.

You can mark duplicate files, but you can never mark the reference file of a group. This is a security measure to prevent dupeGuru from deleting not only duplicate files, but their reference. You sure don't want that, do you?

What determines which files are reference and which files are duplicates is first their folder state. A file from a reference folder will always be reference in a duplicate group. If all files are from a normal folder, the size determine which file will be the reference of a duplicate group. dupeGuru assumes that you always want to keep the biggest file, so the biggest files will take the reference position.

You can change the reference file of a group manually. To do so, select the duplicate file you want
to promote to reference, and click on **Actions-->Make Selected into Reference**.

Reviewing results
-----------------

Although you can just click on **Edit-->Mark All** and then **Actions-->Send Marked to Recycle bin** to quickly delete all duplicate files in your results, it is always recommended to review all duplicates before deleting them.

To help you reviewing the results, you can bring up the **Details panel**. This panel shows all the details of the currently selected file as well as its reference's details. This is very handy to quickly determine if a duplicate really is a duplicate. You can also double-click on a file to open it with its associated application.

In Standard mode the Details panel also shows a **preview** of the selected file beside its reference, so you can compare the two without opening either. Images are shown side by side; anything else shows its icon with the file's details. Use the **Show preview** toggle in the panel to hide it if you would rather have the space for the details table -- the setting is remembered between runs.

If you have more false duplicates than true duplicates (If your filter hardness is very low), the best way to proceed would be to review duplicates, mark true duplicates and then click on **Actions-->Send Marked to Recycle bin**. If you have more true duplicates than false duplicates, you can instead mark all files that are false duplicates, and use **Actions-->Remove Marked from Results**.

Confidence
----------

Not every group is understood to the same degree, and the match percentage does not tell you
which is which -- two files can both sit at 100% while one pair had its contents compared in
full and the other was only sampled. The **Confidence** column (off by default; turn it on from the
**Columns** menu) says what was actually established about each group:

**Corroborated**
    The contents were compared in full, *and* something independent agrees: one copy is in a
    folder you marked as Reference, or every copy in the group has the same filename.

**Content only**
    The contents were compared in full, and that is all that is known. The files really are
    interchangeable, but nothing suggests either copy is the unwanted one -- two documents
    deliberately kept in two projects look exactly like this.

**Unconfirmed**
    The contents were never compared in full. Large files matched on a sampled hash land here,
    as do visually similar pictures, and matches made on names or tags. These may well be
    duplicates; dupeGuru has simply not proven it.

A group is only as understood as its weakest pair, so a group holding one exact match and one
resemblance is Unconfirmed as a whole.

Note what the names deliberately do not say. None of them means *safe to delete* -- that is a
judgement about your files that only you can make. Corroborated is the strongest thing dupeGuru
can claim on its own, not a promise that nothing will be missed.

**Mark-->Mark Corroborated Groups** and **Mark-->Mark Content-Only Groups** mark every duplicate
in the groups at that level, so the review effort can go where it is actually needed. They add
to what is already marked rather than replacing it, so you can apply both and look at the result
before acting. Reference files and files in Reference folders are never marked by either.

On the command line, ``--plan`` reports the same tiers, per group and as a total, so a scripted
cleanup can act on exactly the set you reviewed in the window.

When a scan could not be completed
----------------------------------

Very occasionally dupeGuru runs out of room part way through a scan — most often on a folder
containing a very large number of identical files. When that happens it keeps what it has found
rather than failing outright, and **tells you the results are incomplete** before showing them.

Take that message seriously. The duplicates listed are real, but there are almost certainly more
that were never found, so a folder cleared out on the basis of an incomplete scan is not
finished. Scanning fewer folders at a time usually gets through it.

On the command line the same situation prints a warning to stderr, and the statistics carry
``truncated`` along with a ``truncations`` list naming the stage that stopped. Those fields are
present on every scan, so a script can check whether the results are complete rather than
assuming they are.

Marking and Selecting
---------------------

A **marked** duplicate is a duplicate with the little box next to it having a check-mark. A **selected** duplicate is a duplicate being highlighted. The multiple selection actions can be performed in dupeGuru in the standard way (Shift/Command/Control click). You can toggle all selected duplicates' mark state by pressing **space**.

Show Dupes Only
---------------

When this mode is enabled, the duplicates are shown without their respective reference file. You can select, mark and sort this list, just like in normal mode.

The dupeGuru results, when in normal mode, are sorted according to duplicate groups' **reference file**. This means that if you want, for example, to mark all duplicates with the "exe" extension, you cannot just sort the results by "Kind" to have all exe duplicates together because a group can be composed of more than one kind of files. That is where Dupes Only mode comes into play. To mark all your "exe" duplicates, you just have to:

* Enable the Dupes Only mode.
* Add the "Kind" column with the "Columns" menu.
* Click on that "Kind" column to sort the list by kind.
* Locate the first duplicate with a "exe" kind.
* Select it.
* Scroll down the list to locate the last duplicate with a "exe" kind.
* Hold Shift and click on it.
* Press Space to mark all selected duplicates.

.. _deltavalues:

Delta Values
------------

If you turn this switch on, numerical columns will display the value relative to the duplicate's
reference instead of the absolute values. These delta values will also be displayed in a different
color, orange,  so you can spot them easily. For example, if a duplicate is 1.2 MB and its reference
is 1.4 MB, the Size column will display -0.2 MB.

Moreover, non-numerical values will also be in orange if their value is different from their
reference, and stay black if their value is the same. Combined with column sorting in Dupes Only
mode, this allows for very powerful post-scan filtering.

Dupes Only and Delta Values
---------------------------

The Dupes Only mode unveil its true power when you use it with the Delta Values switch turned on.
When you turn it on, relative values will be displayed instead of absolute ones. So if, for example,
you want to remove from your results all duplicates that are more than 300 KB away from their
reference, you could sort the dupes only results by Size, select all duplicates under -300 in the
Size column, delete them, and then do the same for duplicates over 300 at the bottom of the list.

Same thing for non-numerical values: When Dupes Only and Delta Values are enabled at the same time,
column sorting groups rows depending on whether they're orange or not. Example: You ran a contents
scan, but you would only like to delete duplicates that have the same filename? Sort by filename
and all dupes with their filename attribute being the same as the reference will be grouped
together, their value being in black.

You could also use it to change the reference priority of your duplicate list. When you make a fresh
scan, if there are no reference folders, the reference file of every group is the biggest file. If
you want to change that, for example, to the latest modification time, you can sort the dupes only
results by modification time in **descending** order, select all duplicates with a modification time
delta value higher than 0 and click on **Make Selected into Reference**. The reason why you must
make the sort order descending is because if 2 files among the same duplicate group are selected
when you click on **Make Selected into Reference**, only the first of the list will be made
reference, the other will be ignored. And since you want the last modified file to be reference,
having the sort order descending assures you that the first item of the list will be the last
modified.

Filtering
---------

dupeGuru supports post-scan filtering. With it, you can narrow down your results so you can perform
actions on a subset of it. For example, you could easily mark all duplicates with their filename
containing "copy" from your results using the filter.

To use the filtering feature, type your filter in the "Filter" search field at the top-right corner
of the results window. What you type in that box will be applied to the *whole path* of every
duplicate in the results. Only duplicate *groups* having at least one duplicate matching the filter
will be shown.

When having groups where not all duplicates match the filter, we still show all duplicates of
the group. However, non-matching duplicates are in "reference mode". Therefore, you can perform
actions like "Mark All" and be sure to only mark filtered duplicates.

To go back to unfiltered result, blank out the field or click on the "X".

In simple mode (the default mode), whatever you type as the filter is the string used to perform the
actual filtering, with the exception of one wildcard: **\***. Thus, if you type "[*]" as your
filter, it will match anything with [] brackets in it, whatever is in between those brackets.

For more advanced filtering, you can turn "Use regular expressions when filtering" on. The filtering
feature will then use **regular expressions**. A regular expression is a language for matching text.
Explaining them is beyond the scope of this document. A good place to start learning it is
`regular-expressions.info`_.

Matches are case insensitive in both simple and regexp mode.

For the filter to match, your regular expression don't have to match the whole filename, it just
have to contain a string matching the expression.

Action Menu
-----------

**Clear Ignore List:**
    Remove all ignored matches you added. You have to start a new scan for the
    newly cleared ignore list to be effective.
**Export Results to XHTML:**
    Take the current results, and create an XHTML file out of it. The
    columns that are visible when you click on this button will be the columns present in the XHTML
    file. The file will automatically be opened in your default browser.
**Send Marked to Trash:**
    Send all marked duplicates to trash, obviously. Before proceeding,
    you'll be presented deletion options (see below).
**Move Marked to...:**
    Prompt you for a destination, and then move all marked files to that
    destination. Source file's path might be re-created in destination, depending on the
    "Copy and Move" preference.
**Copy Marked to...:**
    Prompt you for a destination, and then copy all marked files to that
    destination. Source file's path might be re-created in destination, depending on the
    "Copy and Move" preference.
**Remove Marked from Results:**
    Remove all marked duplicates from results. The actual files will
    not be touched and will stay where they are.
**Remove Selected from Results:**
    Remove all selected duplicates from results. Note that all
    selected reference files will be ignored, only duplicates can be removed with this action.
**Make Selected into Reference:**
    Promote all selected duplicates to reference. If a duplicate is
    a part of a group having a reference file coming from a reference folder (in blue color), no
    action will be taken for this duplicate. If more than one duplicate among the same group are
    selected, only the first of each group will be promoted.
**Add Selected to Ignore List:**
    This first removes all selected duplicates from results, and
    then add the match of that duplicate and the current reference in the ignore list. This match
    will not come up again in further scan. The duplicate itself might come back, but it will be
    matched with another reference file. You can clear the ignore list with the Clear Ignore List
    command.
**Open Selected with Default Application:**
    Open the file with the application associated with selected file's type.
**Reveal Selected in Finder:**
    Open the folder containing selected file.
**Invoke Custom Command:**
    Invokes the external application you've set up in your preferences using the current selection
    as arguments in the invocation.
**Rename Selected:**
    Prompts you for a new name, and then rename the selected file.

Folder Overlap
--------------

A large scan can produce thousands of duplicate groups, and reviewing them one at a time is
usually the wrong shape of work: most of them have a single explanation, such as a backup folder
shadowing an original. **Actions --> Folder Overlap...** groups the groups by the folder pair
that explains them.

Each row is one folder pair, with the number of files and the space they would free. Expanding a
row lists the files themselves. **Mark These** marks exactly the files that row counted -- the
number shown is the number marked, and nothing is deleted until you delete it.

A pair is only shown when it explains most of what its folder contributes and covers more than a
handful of files. Two folders that happen to share a few files are a coincidence rather than a
pattern, and presenting that as one decision would invite acting on something that is not there.
Duplicates no pair accounts for are listed separately rather than hidden, so nothing disappears
from view.

The pairing is reported at the level that says the most. Two subfolders both shadowed by the same
backup are shown as the one folder pair above them; a folder whose duplicates all live in one
place is shown against that place rather than generalised up to something uninformative.

**About the direction.** Where you have marked a reference folder, dupeGuru knows which side you
consider the original and shows an arrow. Where you have not, it chose the file to keep in each
group by size, so neither folder is known to be the original -- the two are shown as equals
instead, because an arrow there would be an answer the application invented rather than one you
gave it.

Folder Overlap Report
---------------------

**Actions --> Folder Overlap Report...** answers a different question from the one above: not
"what can I act on" but "what is the shape of this archive". For each scanned folder it reports
how much of its **whole content** also exists somewhere else, and where.

::

    /Volumes/Photos/2023   100% of 437 files    /Volumes/Backup/2023 (437)
    /Volumes/Backup         87% of 500 files    /Volumes/Photos/2023 (437)
    /Users/k/Downloads      39% of 228 files    /Volumes/Photos/misc (88)

There is nothing to press here but Close. It is for working out where to look before deciding
anything; the deciding happens in the results list or in **Folder Overlap**.

Note that the percentage counts everything in the folder, duplicated or not, which is what makes
it different from the rollup's figures. A folder of a thousand files with ten duplicated is a
confident folder pair in the rollup -- all ten resolve to the same place -- and 1% redundant
here. Both are true; they answer different questions.

Only folders dupeGuru actually scanned are listed. If you scanned ``Downloads`` but not the rest
of your home folder, no figure is shown for the home folder, because a percentage of just the
part that was looked at would be misleading rather than approximate.

Folders duplicated in full are called out in the summary line. Those are the ones that could in
principle be removed entirely, which is a stronger statement than "mostly duplicated".

Deletion History
----------------

**File --> Deletion History...** lists what dupeGuru has deleted, grouped by operation, and can
put a run back.

For each file it records where it was, how big it was, what it duplicated, and — for files sent
to the trash — where in the trash it went. That last part is what makes restoring possible: the
trash renames files that collide, so the new name cannot be worked out afterwards and has to be
noted at the time.

The record is written *before* each file is removed, so a crash or a power cut cannot leave you
with a deleted file and no record of it.

Restoring checks before it acts, and refuses rather than guessing:

* If a **different file now occupies the original path**, the file is not restored and you are
  told. Putting the old copy back would destroy the newer one, which is the sort of loss this
  feature exists to prevent.
* If the **trash has been emptied**, there is nothing to put back, and it says so.
* If the file is **already back** — restored by you in the meantime, or by an earlier restore —
  it is left alone and reported as already there.
* Files that were **deleted permanently** were never in the trash and cannot be restored. Those
  runs say so rather than offering a button that would fail.

Whatever could not be restored is listed with the reason, so a partial restore is never reported
as a complete one.

Restoring a file on Windows moves it out of the Recycle Bin directly. The Recycle Bin may go on
listing the entry until it is next emptied, even though the file is back where it belongs --
your file is not affected either way.

If a file's location could not be recorded at the time it was deleted, it is listed as
unrestorable rather than offered and then refused. Use the trash's or Recycle Bin's own restore
for those.

Deletion Options
----------------

These options affect how duplicate deletion takes place. Most of the time, you don't need to enable
any of them.

**Link deleted files:**
    The deleted files are replaced by a link to the reference file. You have a choice of replacing
    it either with a `symlink`_ or a `hardlink`_. It's better to read the whole
    wikipedia pages about them to make a informed choice, but in short, a symlink is a shortcut to
    the file's path. If the original file is deleted or moved, the link is broken. A hardlink is a
    link to the file *itself*. That link is as good as a "real" file. Only when *all* hardlinks to a
    file are deleted is the file itself deleted.

    On OSX and Linux, this feature is supported fully, but under Windows, it's a bit complicated.
    Windows XP doesn't support it, but Vista and up support it. However, for the feature to work,
    dupeGuru has to run with administrative privileges.

**Replace duplicates with copy-on-write clones:**
    Instead of removing a duplicate, replace it with a *clone* of the reference. Both files
    remain, both keep their own name and permissions, and both stay independently editable --
    changing one does not change the other, and deleting one leaves the other whole. The disk
    space is still reclaimed, because the two files share it until one of them is written to.

    This is the only deletion option where nothing is lost, which makes it a good default on
    filesystems that support it: APFS on macOS, and Btrfs or XFS on Linux. It is unavailable
    elsewhere, and the option is disabled rather than falling back to something destructive.

    Two limits are worth knowing. Cloning cannot cross filesystems, so a duplicate on another
    drive is skipped. And it is only offered where the two files' *content digests agree* --
    a picture-mode match that merely looks the same carries no such agreement, and replacing
    it would substitute a different image. Anything that cannot be cloned safely is skipped
    and listed afterwards rather than deleted.

    One oddity: after cloning, ``du`` and Finder still report the full size for both files,
    because each one genuinely references those blocks. The free space on the volume is what
    actually changes.

**Preview...:**
    Shows exactly what the deletion would do, without doing any of it. Every marked file is
    re-checked against the same conditions the deletion itself applies, so the preview cannot
    promise something that is then refused.

    You get the totals -- how many files, how much space -- and a per-file breakdown saying
    what would happen to each one: sent to trash, deleted permanently, replaced by a clone, or
    skipped and why. Files are skipped when they have changed, moved, or been deleted since the
    scan, which is easy to happen if results have been on screen for a while. Matches confirmed
    only by a sampled hash rather than a full comparison are called out too.

    The preview reflects the options as they are currently set, so changing an option and
    previewing again shows the effect of that change. Nothing is modified either way, so it is
    always safe to look first.

**Directly delete files:**
    Instead of sending files to trash, directly delete them. This is used
    for troubleshooting and you normally don't need to enable this unless dupeGuru has problems
    deleting files normally, something that can happens when you try to delete files on network
    storage (NAS).

.. _regular-expressions.info: http://www.regular-expressions.info
.. _hardlink: http://en.wikipedia.org/wiki/Hard_link
.. _symlink: http://en.wikipedia.org/wiki/Symbolic_link
