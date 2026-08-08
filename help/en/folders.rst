Folder Selection
================

The first window you see when you launch dupeGuru is the folder selection window. This windows
contains the basic input dupeGuru needs to start a scan:

* An Application Mode selection
* A Scan Type selection
* Folders to scan

Application Mode
----------------

dupeGuru had three main modes: Standard, Music and Picture.

Standard is for any type of files. This makes this mode the most polyvalent, but it lacks
specialized features other modes have.

Music mode scans only music files, but it supports tags comparison and its results window has many
audio-related informational columns.

Picture mode scans only pictures, but its contents scan type is a powerful fuzzy matcher that can
find pictures that are similar without being exactly the same.

Choosing an application mode not only changes available scan types in the selector below, but also
changes available options in the preferences panel. Thus, if you want to fine tune your scan, be
sure to open the preferences panel **after** you've selected the application mode.

Scan Type
---------

This selector determines the type of the scan we'll do. See :doc:`scan` for details about scan
types.

Folder List
-----------

To add a folder, click on the **+** button. If you added folder before, a popup
menu with a list of recent folders you added will pop. You can click on one of
them to add it directly to your list. If you click on the first item of the
popup menu, **Add New Folder...**, you will be prompted for a folder to add. If
you never added a folder, no menu will pop and you will directly be prompted
for a new folder to add.

An alternate way to add folders to the list is to drag them in the list.

To remove a folder, select the folder to remove and click on **-**. If a subfolder is selected when
you click the button, the selected folder will be set to **excluded** state (see below) instead of
being removed.

Folder states
-------------

Every folder can be in one of these 3 states:

**Normal:**
    Duplicates found in this folder can be deleted.
**Reference:**
    Duplicates found in this folder **cannot** be deleted. Files from this folder can
    only end up in **reference** position in the dupe group. If more than one file from reference
    folders end up in the same dupe group, only one will be kept. The others will be removed from
    the group.
**Excluded:**
    Files in this directory will not be included in the scan.

The default state of a folder is, of course, **Normal**. You can use **Reference** state for a
folder if you want to be sure that you won't delete any file from it.

When you set the state of a directory, all subfolders of this folder automatically inherit this
state unless you explicitly set a subfolder's state.

System and application locations
--------------------------------

If a folder you have selected is somewhere the operating system or an installed application
keeps its own files -- ``/System``, ``/Applications`` and ``~/Library`` on macOS, ``C:\Windows``
and ``Program Files`` on Windows, ``/usr`` and ``/etc`` on Linux, or the inside of an
application bundle -- dupeGuru says so before it starts scanning, and asks whether to continue.

These places hold many identical files on purpose: shared libraries, bundled resources, cached
copies. Removing them or replacing them with links can stop installed software from working,
often long after the fact and with nothing obviously connecting the two.

It is a warning and not a refusal. Clearing out a duplicate-ridden application-support folder is
a perfectly reasonable thing to do, and only you can say whether it is what you meant. Answer
yes and the scan proceeds as normal; dupeGuru will not ask about that folder again until you
restart it, so re-scanning while you adjust your filters does not mean answering the same
question over and over.

The list of locations is deliberately short, covering only places where deleting duplicates is
known to break things. Your own folders -- Documents, Pictures, external drives -- never trigger
it.

On the command line the same locations produce a warning on stderr rather than a prompt. A scan
deletes nothing on its own, and ``--delete`` has its own confirmation.

Scan Profiles
-------------

If you scan the same folders repeatedly, **File --> Save Scan Profile...** remembers the whole
setup under a name you choose: the folders, their **Normal**/**Reference**/**Excluded** states,
the application mode, the scan type, and the scanning options from your preferences. **File -->
Scan Profiles...** lists what you have saved and loads one back.

Loading a profile replaces the current folder selection rather than adding to it, so what you
see afterwards is exactly what the profile describes and nothing else.

Saving under a name you have already used replaces that profile, so refining a setup and saving
it again does not leave you with several near-identical entries.

Only settings that affect what a scan *finds* are stored. Appearance, language and window layout
are not, so loading a profile will not restyle the application. Neither is the copy/move
destination setting, which applies to what you do with results rather than to finding them.

If a profile refers to folders that no longer exist -- an external drive that is not plugged in,
say -- the list marks it as having missing folders before you load it, and hovering the entry
shows which ones. Loading it anyway scans the folders that are present and tells you which were
skipped. It matters that you are told: a scan covering four folders instead of five simply finds
fewer duplicates, and a short list of duplicates looks the same as a clean one.

Scan
----

When you're ready, click on the **Scan** button to initiate the scanning process. When it's done,
you'll be shown the :doc:`results`.
