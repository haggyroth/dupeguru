from hscommon.gui.base import GUIObject


class MarkDialog(GUIObject):
    """Model for the 'Mark by Rule' dialog.

    Builds a flat list of all available criteria from the app's prioritization
    categories and exposes ``apply()`` to perform the rule-based marking.
    """

    def __init__(self, app):
        GUIObject.__init__(self)
        self.app = app
        self.selected_index = 0
        self._rules = self._build_rules()

    # --- Private
    def _build_rules(self):
        rules = []
        for cat_class in self.app._prioritization_categories():
            cat = cat_class(self.app.results)
            for crit in cat.criteria_list():
                rules.append(crit)
        return rules

    # --- Public
    @property
    def rule_names(self):
        """Ordered display names for all available rules."""
        return [r.display for r in self._rules]

    def apply(self):
        """Reprioritize groups by the selected rule and mark all non-keepers."""
        if not self._rules:
            return
        crit = self._rules[self.selected_index]
        self.app.mark_by_criterion(crit.sort_key)
