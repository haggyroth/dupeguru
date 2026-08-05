# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""Controls that carry no visible text must still be announceable (issue #81).

Qt derives a widget's accessible name from its text() when accessibleName() is empty. A
button whose entire content is an icon has neither, so a screen reader announces "button"
and nothing else. Before this, no widget in the tree set an accessible name at all.

Each test asserts the *absence of text* first. Without that, the test would keep passing
against a button that had gained a visible label and silently stopped needing the name --
guarding nothing while looking green.
"""

import pytest

pytest.importorskip("qtpy", reason="these construct real widgets")


ICON_ONLY_BUTTONS = ["addFolderButton", "removeFolderButton"]


@pytest.mark.parametrize("name", ICON_ONLY_BUTTONS)
def test_directories_dialog_icon_buttons_are_announceable(dgapp, name):
    """Adding a folder is the first thing anyone does; these are the main window's controls."""
    button = getattr(dgapp.directories_dialog, name)
    assert not button.text(), f"{name} has visible text now; drop it from ICON_ONLY_BUTTONS"
    assert button.accessibleName(), f"{name} has neither text nor an accessible name"
    assert button.toolTip(), f"{name} has no tooltip"


def test_search_clear_button_is_announceable(qapp):
    from qt.search_edit import LineEditButton

    button = LineEditButton(None)
    assert not button.text()
    assert button.accessibleName()


def test_color_picker_button_is_announceable(qapp):
    """Its entire content is a colour swatch drawn as an icon."""
    from qt.preferences_dialog import ColorPickerButton

    button = ColorPickerButton(None)
    assert not button.text()
    assert button.accessibleName()


def test_image_viewer_swap_button_is_translatable(qapp):
    """This one was already announceable via text(); the text just was not translatable.

    Kept as a test because "has text" is what makes it accessible, so losing the text without
    gaining an accessible name would be a regression.
    """
    import inspect
    from qt.pe import image_viewer

    source = inspect.getsource(image_viewer.ViewerToolBar.createButtons)
    assert 'setText(tr("Swap images"))' in source, "the swap button's label is not translatable"
