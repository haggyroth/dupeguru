Preferences
===========

**Tags to scan:**
    When using the **Tags** scan type, you can select the tags that will be used for comparison.

**Word weighting:**
    See :ref:`word-weighting`.

**Match similar words:**
    See :ref:`similarity-matching`.

**Match pictures of different dimensions:**
    If you check this box, pictures of different dimensions will be allowed in the same
    duplicate group.

**Match pictures of different rotations:**
    If you check this box, pictures of different rotations will be allowed in the same
    duplicate group.

.. _filter-hardness:

**Filter Hardness:**
    The threshold needed for two files to be considered duplicates. A lower value means more
    duplicates. The meaning of the threshold depends on the scanning type (see :doc:`scan`).
    Only works for :ref:`worded <worded-scan>` and :ref:`picture blocks <picture-blocks-scan>`
    scans.

**Can mix file kind:**
    If you check this box, duplicate groups are allowed to have files with different extensions. If
    you don't check it, well, they aren't!

**Also find visually similar pictures:**
    A standard scan compares file contents, so it finds byte-for-byte copies and nothing else --
    two photographs that are the same picture at different sizes are simply not duplicates to
    it. Picture mode finds those, but collects only images, so pointing dupeGuru at a folder of
    documents, videos and photographs means choosing which half of the problem to look at.

    With this on, a Contents scan also compares the images it finds by appearance and merges the
    results, so one scan covers both.

    It is off by default because it is slow. Comparing contents mostly reads files; comparing
    appearance decodes every image and compares each against the others, which grows sharply
    with the number of pictures.

    Matches found this way are a *resemblance*, not proof of identity: a re-encode, a crop or a
    resize can look identical while the files differ. dupeGuru records which kind each match was
    and never treats a resemblance as a reason to replace a file with a copy-on-write clone.

**Ignore duplicates hardlinking to the same file:**
    If this option is enabled, dupeGuru will verify duplicates to see if they refer to the same
    `inode`_. If they do, they will not be considered duplicates. (Only for OS X and Linux)

**Partially hash files bigger than:**
    Above this size, dupeGuru compares three sampled chunks of a file instead of reading it
    from end to end. Large scans get much faster, but it is a real trade: two different files
    can agree on every sampled chunk and still be reported as duplicates. Such a pair still
    scores 100%, so the match percentage alone will not tell you. Set it to 0 to always
    compare full contents.

**Verify partially hashed matches by comparing full contents:**
    Re-reads only the files involved in a partial match and discards any pair that does not
    match in full. This gives you the speed of partial hashing with the certainty of a full
    comparison, at the cost of reading the matched files a second time. Has no effect unless
    partial hashing is enabled above.

**Compare contents byte for byte before deleting:**
    dupeGuru decides two files are identical by comparing digests -- short summaries of the
    contents, rather than the contents themselves. Two different files sharing a digest is
    vanishingly unlikely with the hash normally in use, but a digest is still a claim about a
    file rather than the file.

    With this on, each file is read and compared directly against the one being kept,
    immediately before it is deleted. Anything that turns out to differ is refused and reported
    instead of removed.

    Off by default, because it doubles the reading a deletion does. Worth turning on when you
    are deleting something you could not replace.

    It applies only where the match claimed identical *contents*. Picture matches are
    resemblances -- a resized or re-encoded copy is meant to differ -- so they are unaffected,
    as are matches made on names or tags. On the command line the same thing is ``--verify``.

**Remember scan results between scans:**
    Reuses what the previous scan found when nothing has changed: folder listings, and in
    Picture mode the comparison results too. Re-reading folders is the slow part of scanning
    an external or network drive, and comparing a large photo library is slower still, so a
    repeat scan of an unchanged drive becomes close to instant.

    Files added, removed or renamed are still noticed. A file edited *in place* without its
    folder changing may be missed until the next full scan, which is why this is off by
    default. Nothing is ever deleted on the basis of remembered information -- every file is
    re-checked against the disk immediately before it is removed.

**Use regular expressions when filtering:**
    If you check this box, the filtering feature will treat your filter query as a
    **regular expression**. Explaining them is beyond the scope of this document. A good place to
    start learning it is `regular-expressions.info`_.

**Remove empty folders after delete or move:**
    When this option is enabled, folders are deleted after a file is deleted or moved and the folder
    is empty.

**Copy and Move:**
    Determines how the Copy and Move operations (in the Action menu) will behave.

* **Right in destination:** All files will be sent directly in the selected destination, without
  trying to recreate the source path at all.
* **Recreate relative path:** The source file's path will be re-created in the destination folder up
  to the root selection in the Directories panel. For example, if you added
  ``/Users/foobar/SomeFolder`` to your Directories panel and you move
  ``/Users/foobar/SomeFolder/SubFolder/SomeFile.ext`` to the destination
  ``/Users/foobar/MyDestination``, the final destination for the file will be
  ``/Users/foobar/MyDestination/SubFolder`` (``SomeFolder`` has been trimmed from source's path in
  the final destination.).
* **Recreate absolute path:** The source file's path will be re-created in the destination folder in
  its entirety. For example, if you move ``/Users/foobar/SomeFolder/SubFolder/SomeFile.ext`` to the
  destination ``/Users/foobar/MyDestination``, the final destination for the file will be
  ``/Users/foobar/MyDestination/Users/foobar/SomeFolder/SubFolder``.

In all cases, dupeGuru nicely handles naming conflicts by prepending a number to the destination
filename if the filename already exists in the destination.

**Custom Command:**
    This preference determines the command that will be invoked by the "Invoke Custom Command"
    action. You can invoke any external application through this action. This can be useful if,
    for example, you have a nice diffing application installed.

The format of the command is the same as what you would write in the command line, except that there
are 2 placeholders: **%d** and **%r**. These placeholders will be replaced by the path of the
selected dupe (%d) and the path of the selected dupe's reference file (%r).

If the path to your executable contains space characters, you should enclose it in "" quotes. You
should also enclose placeholders in quotes because it's very possible that paths to dupes and refs
will contain spaces. Here's an example custom command::

    "C:\Program Files\SuperDiffProg\SuperDiffProg.exe" "%d" "%r"

.. _inode: http://en.wikipedia.org/wiki/Inode
.. _regular-expressions.info: http://www.regular-expressions.info
