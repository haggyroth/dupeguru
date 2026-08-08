Re-Prioritizing duplicates
==========================

dupeGuru tries to automatically determine which duplicate should go in each group's reference
position, but sometimes it gets it wrong. In many cases, clever dupe sorting with "Delta Values"
and "Dupes Only" options in addition to the "Make Selected into Reference" action does the trick,
but sometimes, a more powerful option is needed. This is where the Re-Prioritization dialog comes
into play. You can summon it through the "Re-Prioritize Results" item in the "Actions" menu.

This dialog allows you to select criteria according to which a reference dupe will be selected in
each dupe group. The list of available criteria is on the left and the list of criteria you've
selected is on the right.

A criteria is a category followed by an argument. For example, "Size (Highest)" means that the dupe
with the biggest size will win. "Folder (/foo/bar)" means that dupes in this folder will win. To add
a criterion to the rightmost list, first select a category in the combobox, then select a
subargument in the list below, and then click on the right pointing arrow button.

The order of the list on the right is important (you can re-order items through drag & drop). When
picking a dupe for reference position, the first criterion is used. If there's a tie, the second
criterion is used and so on and so on. For example, if your arguments are "Size (Highest)" and then
"Filename (Doesn't end with a number)", the reference file that will be picked in a group will be
the biggest file, and if two or more files have the same size, the one that has a filename that
doesn't end with a number will be used. When all criteria result in ties, the order in which dupes
previously were in the group will be used.

Available criteria
------------------

Which categories are offered depends on the application mode. Kind, Folder, Filename, Size and
Modification are available everywhere. Music mode adds Duration, Bitrate and Samplerate. Picture
mode adds Dimensions and EXIF Timestamp.

EXIF Timestamp
^^^^^^^^^^^^^^

Picture mode only, and worth understanding before you use it: this orders photos by when they were
**taken**, not when the file was last written.

That distinction matters because copying, exporting, syncing and restoring from a backup all reset
a file's modification date. The copy routinely looks newer than the original, so "Modification
(Oldest)" keeps the wrong file. The capture date recorded by the camera does not move, so it
survives all of those.

The argument is **Newest** or **Oldest** rather than Highest or Lowest, since it is a date.

Photos with no usable capture date -- screenshots, scans, exports that dropped their EXIF, and
cameras whose clock was never set -- sort **last** under both Newest and Oldest. They are never
treated as the oldest photo ever taken, which is what would happen if a missing date were read as
zero. If a group contains nothing but photos without capture dates, this criterion cannot separate
them and the next criterion in your list decides.
