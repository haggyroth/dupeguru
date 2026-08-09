Exclusion Filters
=================

Exclusion filters keep files and folders out of a scan entirely. dupeGuru never looks at
something a filter excludes, so it is faster than scanning everything and ignoring the results
afterwards, and it is the right place to keep build folders, caches and OS metadata out of the
way.

Open the list with **View → Exclusion Filters**.

This is a different mechanism from :doc:`folder states <folders>`. A folder marked *Excluded*
takes one folder out of one scan; an exclusion filter is a rule applied to every folder and
every file in every scan.

How a rule is matched
---------------------

Each rule is a **Python regular expression**, not a shell wildcard. ``*.tmp`` does not mean
what it does in a file manager — in a regular expression ``*`` repeats whatever came before it,
and a bare ``*.tmp`` is not even a valid pattern.

Two things about the matching are worth knowing before you write a rule, because getting either
wrong produces a rule that is accepted, listed as active, and excludes nothing at all:

**Rules are anchored.** A rule must match the *whole* name or the *whole* path, not a piece of
it. ``node_modules`` matches a folder called exactly ``node_modules``; it does not match
``/home/me/project/node_modules``. To match a fragment of something longer, put ``.*`` on both
ends.

**A rule is matched against either the filename or the full path**, and which one depends on
whether it contains a path separator. A rule with no separator is matched against the bare
name of each file and folder. A rule containing one is matched against the full path. That is
why ``.*node_modules.*`` — no separator — works: it is compared against each folder's own name.

Writing a rule on Windows
-------------------------

On Windows the path separator is a backslash, and a backslash is also the escape character in a
regular expression. A rule has to use **two** backslashes for every separator you want.

This matters more than it sounds, because writing one backslash — the way Windows displays
every path — fails silently. Take a file at ``C:\Users\me\excluded\sub\c.txt``:

.. list-table::
    :header-rows: 1
    :widths: 40 60

    * - Rule
      - Result
    * - ``.*excluded\\sub\\.*``
      - Excludes the files under ``excluded\sub``
    * - ``excluded\\sub``
      - Matches nothing — not anchored, needs ``.*``
    * - ``.*excluded\sub\.*``
      - Matches nothing — single backslashes
    * - ``c\.txt``
      - Excludes every file named ``c.txt``

The third row fails twice over. With only single backslashes the rule contains no separator, so
it is treated as a *filename* pattern and compared against ``c.txt``, which a path can never
match. And the backslash is still consumed as an escape: ``\s`` is the regular-expression class
for whitespace, so the rule actually reads "``excluded``, a space, ``ub``".

So a working Windows path rule needs both corrections — doubled separators *and* ``.*`` at each
end. Neither is announced: a rule missing them compiles cleanly and sits in the list looking
exactly like one that works.

On macOS and Linux the separator is ``/``, which is not an escape character, so a path rule
needs only the ``.*`` anchors: ``.*/node_modules/.*``.

Some rules that work
--------------------

.. list-table::
    :header-rows: 1
    :widths: 40 60

    * - Rule
      - Excludes
    * - ``^\.git$``
      - Git repository folders, by name, anywhere
    * - ``.*\.tmp``
      - Every file ending in ``.tmp``
    * - ``node_modules``
      - Folders named exactly ``node_modules``
    * - ``.*/Library/Caches/.*``
      - Anything under a ``Library/Caches`` folder (macOS)
    * - ``.*\\AppData\\Local\\Temp\\.*``
      - Anything in the Windows per-user temp folder

Testing a rule
--------------

Because a wrong rule fails quietly, check a new one rather than assuming it works. The dialog
has a second text box for exactly this: type a real path or filename into it and press
**Test string**. Every rule that matches is highlighted in the list.

This is the fastest way to see the traps above for yourself. With ``C:\Users\me\excluded\sub\c.txt``
in the test box, ``.*excluded\\sub\\.*`` highlights and ``.*excluded\sub\.*`` does not.

A rule that highlights nothing excludes nothing.

Default filters
---------------

The list starts with rules for files that are never worth comparing — ``Thumbs.db``,
``desktop.ini``, ``.DS_Store``, trash and recycle folders, and anything whose name begins with a
dot. **Restore defaults** puts them back if you remove them.

A few patterns are refused rather than added, because they would exclude everything and leave a
scan with nothing to compare: ``.*`` and its close relatives.

Unchecking instead of removing
------------------------------

Each rule has a checkbox. Unchecking it keeps the rule in the list but stops applying it, which
is more convenient than deleting a rule you expect to want again — and avoids retyping a Windows
path pattern.

From the command line
---------------------

``dupeguru-scan`` takes the same kind of rules; see :doc:`command_line`.
