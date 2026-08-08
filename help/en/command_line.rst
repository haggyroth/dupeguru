Command Line
============

dupeGuru ships a command-line scanner, ``dupeguru-scan``, alongside the application. It finds
duplicates and reports them as JSON, and can plan or perform a deletion without a window ever
opening — useful for scripting a cleanup, running one over SSH, or scheduling it.

It is installed with the application; there is nothing extra to download.

Where to find it
----------------

**macOS** — inside the application bundle, so dragging dupeGuru to your Applications folder
brings it along::

    /Applications/dupeGuru.app/Contents/Resources/cli/dupeguru-scan/dupeguru-scan

That is a mouthful to type. If you plan to use it regularly, link it somewhere on your ``PATH``::

    ln -s "/Applications/dupeGuru.app/Contents/Resources/cli/dupeguru-scan/dupeguru-scan" \
          /usr/local/bin/dupeguru-scan

Deleting the application removes the command line with it.

**Windows** — in a ``cli`` folder inside the install directory, typically::

    C:\Program Files\dupeGuru\cli\dupeguru-scan\dupeguru-scan.exe

Add that folder to your ``PATH`` if you want to call it by name. Uninstalling dupeGuru removes
it.

Getting started
---------------

``--help`` lists every option with a description::

    dupeguru-scan --help

A scan prints JSON on standard output and progress on standard error, so the results can be
piped somewhere while you still see what is happening::

    dupeguru-scan ~/Pictures > duplicates.json

Nothing is deleted unless you ask. ``--plan`` reports exactly what a deletion would do —
which files, how much space, and what would be refused and why — without touching anything.

Keeping a record of what was deleted
------------------------------------

A deletion reports what it removed, so an unattended run leaves an account of itself rather
than just an exit code. The record replaces the usual results, and goes to ``--output`` when
you give one::

    dupeguru-scan ~/Pictures --delete --yes --output deleted.json

Every entry carries the file, its size, the file it duplicated, and — when the platform can
report it — where it went in the trash::

    {
      "deleted": [
        {
          "path": "/Users/me/Pictures/holiday copy.jpg",
          "size": 2411984,
          "reference": "/Users/me/Pictures/holiday.jpg",
          "destination": "/Users/me/.Trash/holiday copy.jpg",
          "permanent": false,
          "restorable": true
        }
      ],
      "skipped": [],
      "stats": {"deleted": 1, "reclaimed_bytes": 2411984, "restorable": 1, ...}
    }

``destination`` is where the file actually landed, read back from the operating system rather
than guessed, which is what makes putting it back possible. ``restorable`` says whether that
is worth attempting: a permanent deletion with ``--direct-delete`` records no destination and
reports ``false``, as does a trashed file on a system that cannot say where it put it.

Files that could not be deleted — changed since the scan, already gone, permission denied —
appear under ``skipped`` with the reason, so a partly failed run still says exactly what did
and did not happen.

With ``--ndjson`` the same information arrives one JSON object per line, ending with the stats
record.

Reviewing the biggest wins first
--------------------------------

A scan of a large disk can return thousands of groups, and reviewing them in the order they were
found means most of your attention goes to files that free almost nothing.

``--sort-by reclaimable`` ranks groups by the space deleting them would actually free::

    dupeguru-scan ~/Pictures --sort-by reclaimable

That is not the same as ranking by file size. Reclaimable space is what the *duplicates* free —
the reference stays — so six 700 MB duplicates reclaim more than two 4 GB ones. Every group
carries ``reclaimable_bytes``, and where a group was matched only on a sampled hash, the portion
that is not fully confirmed is reported separately as ``reclaimable_partial_bytes``.

The statistics always carry a cumulative curve, whichever order you asked for, so you can see how
much of the benefit sits at the top of the list::

    first  10 groups ->  292895 bytes  (76.8%)
    first  20 groups ->  359768 bytes  (94.3%)
    first  25 groups ->  381587 bytes  (100.0%)

Reviewing ten of those twenty-five groups gets three quarters of the space. In the order the
scanner found them, the same ten groups would have given twenty per cent — which is the whole
argument for the flag.

What it does not do
-------------------

**Picture mode is unavailable.** ``--mode picture`` needs an image decoder, which means a Qt
installation, and bundling one would have made this download around eight times larger for a
feature most command-line users do not want. Running it says so and exits::

    $ dupeguru-scan ~/Pictures --mode picture
    Picture mode needs a Qt binding for image decoding, and none could be imported.
    Install one with: pip install -r requirements.txt

A standard scan that has been asked to also find visually similar images behaves the same way,
except that it carries on: it reports the exact duplicates it found and warns that the picture
matching was skipped.

Both work normally in the application, and both work from a source checkout with a Qt binding
installed. Only the packaged command line leaves the decoder out.

**There is no interactive prompt.** Anything the application would ask about, the command line
either refuses or requires you to allow explicitly — ``--delete`` needs ``--yes``, and deleting
files matched only on a sampled hash needs ``--allow-partial-matches``. A confirmation nobody
can answer is treated as "no", so an unattended run never destroys anything by default.
