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
